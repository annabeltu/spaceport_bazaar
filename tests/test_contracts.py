"""
Checks on the contracts: the shared names every client package builds against.

Package A wrote the data types (models.py, errors.py) in full, and every other
module in src/bazaar_client/ as a stub: real names, parameters and return
types, with a body that raises NotImplementedError until the package that owns
it fills it in. Once A merges, those names are FROZEN, because packages D-I are
built against them at the same time. The tests here fail if anyone renames a
field or changes a type.

These tests never call a stub, so they stay valid after the bodies are written.
"""
import dataclasses
import importlib
import inspect
import logging
import typing
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from bazaar_client import (
    codec,
    commands,
    connection,
    credentials,
    engine,
    errors,
    guards,
    logs,
    runner,
    state,
)
from bazaar_client.connection import Connection
from bazaar_client.credentials import Credentials
from bazaar_client.engine import Decision, Finish, Position, Send, Stop, Wait
from bazaar_client.models import MAX_UINT64, Amounts, ClientState, GuardContext
from bazaar_client.runner import RunOptions
from generated import bazaar_pb2 as pb

# `__main__` is an awkward name to write in an import line, so load it by name.
cli = importlib.import_module("bazaar_client.__main__")


def is_frozen_dataclass(cls):
    """Whether `cls` is a dataclass whose fields can't be reassigned."""
    return dataclasses.is_dataclass(cls) and cls.__dataclass_params__.frozen


def field_types(cls):
    """(name, type) for each field of a dataclass, in order."""
    return tuple((field.name, field.type) for field in dataclasses.fields(cls))


# --- pytest setup ---------------------------------------------------------------


def test_live_marker_is_registered(pytestconfig):
    # Package K marks tests that start the real server with @pytest.mark.live.
    # Because of --strict-markers, an unregistered marker is an error.
    markers = pytestconfig.getini("markers")
    assert any(line.startswith("live:") for line in markers)


# --- models.py ------------------------------------------------------------------


@pytest.mark.parametrize("cls", [Amounts, GuardContext, ClientState])
def test_model_is_a_frozen_dataclass(cls):
    assert is_frozen_dataclass(cls)


def test_changing_a_frozen_model_raises():
    amounts = Amounts(water=1, food=2, components=3)
    with pytest.raises(dataclasses.FrozenInstanceError):
        amounts.water = 99


def test_model_fields_match_the_contract():
    assert field_types(Amounts) == (("water", int), ("food", int), ("components", int))
    assert field_types(GuardContext) == (
        ("run_id", str),
        ("is_ready", bool),
        ("max_command_bytes", int),
        ("inventory", Amounts),
        ("sent_requests", Mapping[str, bytes]),
    )
    assert field_types(ClientState) == (
        ("run_id", str | None),
        ("snapshot", pb.State | None),
        ("is_ready", bool),
        ("results", Mapping[str, pb.Result]),
        ("sent_requests", Mapping[str, bytes]),
    )


@pytest.mark.parametrize(
    "partial_amounts",
    [
        {"food": 0, "components": 0},
        {"water": 0, "components": 0},
        {"water": 0, "food": 0},
        {},
    ],
    ids=["no water", "no food", "no components", "nothing"],
)
def test_amounts_cannot_be_built_without_all_three(partial_amounts):
    # No defaults on purpose: the spec requires every amount, zeros included.
    with pytest.raises(TypeError):
        Amounts(**partial_amounts)


def test_amounts_accepts_zero_and_the_largest_64_bit_value():
    amounts = Amounts(water=0, food=MAX_UINT64, components=0)
    assert amounts.food == MAX_UINT64


@pytest.mark.parametrize("bad_amount", [-1, MAX_UINT64 + 1])
def test_amounts_rejects_values_that_dont_fit_in_uint64(bad_amount):
    with pytest.raises(ValueError, match="water"):
        Amounts(water=bad_amount, food=0, components=0)


@pytest.mark.parametrize("bad_amount", [True, 1.5, "2", None])
def test_amounts_rejects_values_that_arent_whole_numbers(bad_amount):
    # True counts as the number 1 in Python, which would hide a bug.
    with pytest.raises(TypeError, match="components"):
        Amounts(water=0, food=0, components=bad_amount)


def test_amounts_to_bundle_sets_every_field_including_zeros():
    bundle = Amounts(water=2, food=0, components=0).to_bundle()
    assert bundle.IsInitialized(), bundle.FindInitializationErrors()
    assert (bundle.water, bundle.food, bundle.components) == (2, 0, 0)


def test_amounts_to_bundle_returns_a_new_bundle_each_time():
    amounts = Amounts(water=1, food=1, components=1)
    first = amounts.to_bundle()
    first.water = 50
    assert amounts.to_bundle().water == 1


def test_amounts_survive_a_round_trip_through_a_bundle():
    amounts = Amounts(water=28, food=31, components=MAX_UINT64)
    assert Amounts.from_bundle(amounts.to_bundle()) == amounts


# --- errors.py ------------------------------------------------------------------

ERROR_CLASSES = (
    errors.GuardError,
    errors.ProtocolViolation,
    errors.CredentialsError,
    errors.ConnectionFailed,
    errors.ConnectionLost,
)


@pytest.mark.parametrize("error_class", ERROR_CLASSES, ids=lambda cls: cls.__name__)
def test_error_is_an_exception_with_a_docstring(error_class):
    assert issubclass(error_class, Exception)
    assert error_class.__doc__


@pytest.mark.parametrize("error_class", ERROR_CLASSES, ids=lambda cls: cls.__name__)
def test_errors_are_separate_kinds(error_class):
    # The runner reacts to each kind differently, so `except GuardError` must
    # never also catch, say, a ProtocolViolation.
    others = [other for other in ERROR_CLASSES if other is not error_class]
    assert not any(issubclass(error_class, other) for other in others)


# --- Every module in the plan's layout exists -----------------------------------

MODULE_NAMES = (
    "bazaar_client",
    "bazaar_client.__main__",
    "bazaar_client.models",
    "bazaar_client.errors",
    "bazaar_client.codec",
    "bazaar_client.commands",
    "bazaar_client.guards",
    "bazaar_client.state",
    "bazaar_client.credentials",
    "bazaar_client.connection",
    "bazaar_client.engine",
    "bazaar_client.runner",
    "bazaar_client.logs",
)


@pytest.mark.parametrize("module_name", MODULE_NAMES)
def test_module_imports_and_explains_itself(module_name):
    module = importlib.import_module(module_name)
    assert module.__doc__, f"{module_name} needs a docstring saying what it does"


# --- Function signatures --------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Signature:
    """What one public function's signature must look like."""

    function: typing.Callable
    parameters: tuple  # (name, type) pairs in order, leaving out `self`
    returns: object
    is_async: bool = False


def signature_id(expected):
    """Name each test case after the function, e.g. `state.record_sent`."""
    module = expected.function.__module__.removeprefix("bazaar_client.")
    return f"{module}.{expected.function.__qualname__}"


# The first two parameters of every builder for a trading command.
RUN_AND_REQUEST_ID = (("run_id", str), ("request_id", str))

SIGNATURES = (
    Signature(codec.encode, (("message", pb.ClientMessage),), bytes),
    Signature(codec.decode, (("frame", bytes),), pb.ServerMessage),
    Signature(
        commands.build_ready,
        (("run_id", str), ("snapshot_sequence", int), ("ready", bool)),
        pb.ClientMessage,
    ),
    Signature(
        commands.build_advertise,
        (*RUN_AND_REQUEST_ID, ("selling", tuple[int, ...]), ("seeking", tuple[int, ...]),
         ("expires_tick", int)),
        pb.ClientMessage,
    ),
    Signature(
        commands.build_offer,
        (*RUN_AND_REQUEST_ID, ("recipient_id", str), ("give", Amounts), ("receive", Amounts),
         ("expires_tick", int)),
        pb.ClientMessage,
    ),
    Signature(commands.build_accept, (*RUN_AND_REQUEST_ID, ("offer_id", str)), pb.ClientMessage),
    Signature(commands.build_withdraw, (*RUN_AND_REQUEST_ID, ("object_id", str)), pb.ClientMessage),
    Signature(commands.build_sync, (("run_id", str),), pb.ClientMessage),
    Signature(guards.check, (("message", pb.ClientMessage), ("context", GuardContext)), bytes),
    Signature(state.initial, (), ClientState),
    Signature(
        state.apply_server_message,
        (("client", ClientState), ("message", pb.ServerMessage)),
        ClientState,
    ),
    Signature(
        state.record_sent,
        (("client", ClientState), ("request_id", str), ("data", bytes)),
        ClientState,
    ),
    Signature(state.on_new_connection, (("client", ClientState),), ClientState),
    Signature(state.guard_context, (("client", ClientState),), GuardContext),
    Signature(
        credentials.load_credentials, (("path", Path), ("station_id", str)), Credentials
    ),
    Signature(
        connection.connect,
        (("url", str), ("credentials", Credentials)),
        Connection,
        is_async=True,
    ),
    Signature(Connection.send, (("data", bytes),), None, is_async=True),
    Signature(Connection.receive, (), bytes, is_async=True),
    Signature(Connection.close, (), None, is_async=True),
    Signature(engine.first_position, (), Position),
    Signature(
        engine.decide,
        (("client", ClientState), ("position", Position), ("received", pb.ServerMessage)),
        tuple[Position, Decision],
    ),
    Signature(runner.run, (("options", RunOptions),), int, is_async=True),
    Signature(cli.parse_args, (("argv", Sequence[str]),), RunOptions),
    Signature(cli.main, (("argv", Sequence[str] | None),), int),
    Signature(logs.redact, (("text", str), ("token", str | None)), str),
    Signature(
        logs.setup_logging,
        (("log_file", Path | None), ("token", str | None)),
        logging.Logger,
    ),
    Signature(
        logs.log_sent, (("logger", logging.Logger), ("message", pb.ClientMessage)), None
    ),
    Signature(
        logs.log_received,
        (("logger", logging.Logger), ("message", pb.ServerMessage)),
        None,
    ),
)


def parameters_of(function):
    """(name, type) for each of a function's parameters, in order, minus `self`."""
    return tuple(
        (parameter.name, parameter.annotation)
        for parameter in inspect.signature(function).parameters.values()
        if parameter.name != "self"
    )


@pytest.mark.parametrize("expected", SIGNATURES, ids=signature_id)
def test_parameters_match_the_contract(expected):
    assert parameters_of(expected.function) == expected.parameters


@pytest.mark.parametrize("expected", SIGNATURES, ids=signature_id)
def test_return_type_matches_the_contract(expected):
    assert inspect.signature(expected.function).return_annotation == expected.returns


@pytest.mark.parametrize("expected", SIGNATURES, ids=signature_id)
def test_async_exactly_where_the_contract_says(expected):
    # Calling an async function without `await` silently does nothing, so a
    # function switching between async and plain would break its callers.
    assert inspect.iscoroutinefunction(expected.function) == expected.is_async


@pytest.mark.parametrize("expected", SIGNATURES, ids=signature_id)
def test_function_explains_itself(expected):
    assert expected.function.__doc__


# --- Data types outside models.py -----------------------------------------------

DATA_TYPES = (
    (Credentials, (("station_id", str), ("run_id", str), ("token", str))),
    (
        RunOptions,
        (("credentials_path", Path), ("url", str), ("dry_run", bool),
         ("log_file", Path | None)),
    ),
    (Send, (("message", pb.ClientMessage),)),
    (Wait, ()),
    (Finish, (("summary", str),)),
    (Stop, (("reason", str),)),
)


@pytest.mark.parametrize(
    ("cls", "fields"), DATA_TYPES, ids=[cls.__name__ for cls, _ in DATA_TYPES]
)
def test_data_type_is_frozen_with_the_contracts_fields(cls, fields):
    assert is_frozen_dataclass(cls)
    assert field_types(cls) == fields


def test_position_is_a_frozen_dataclass():
    # Package H picks Position's fields, so only its frozen-ness is checked here.
    assert is_frozen_dataclass(Position)


def test_decision_is_one_of_four_kinds():
    assert typing.get_args(Decision) == (Send, Wait, Finish, Stop)


def test_printing_credentials_never_shows_the_token():
    fake_token = "not-a-real-token"
    loaded = Credentials(station_id="P01", run_id="test-run-1", token=fake_token)
    assert fake_token not in repr(loaded)
    assert fake_token not in str(loaded)
    assert fake_token not in f"{loaded}"


# --- Constants ------------------------------------------------------------------


def test_connection_constants_match_the_spec():
    assert connection.SUBPROTOCOL == "bazaar.protobuf.v2"
    assert connection.DEFAULT_URL == "ws://127.0.0.1:3001/ws"


def test_log_labels_are_fixed():
    # Packages J and K count "SENT" and "RECEIVED" lines in the client's log
    # (the spec expects 8 sent and 16 received), so these must not change.
    assert logs.REDACTED == "[REDACTED]"
    assert logs.SENT_LABEL == "SENT"
    assert logs.RECEIVED_LABEL == "RECEIVED"
