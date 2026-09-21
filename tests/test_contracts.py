"""
Checks on the contracts: the shared names every client package builds against.

Package A wrote the data types (models.py, errors.py) in full, and every other
module in src/bazaar_client/ as a stub: real names, parameters and return
types, with a body that raises NotImplementedError until the package that owns
it fills it in. Once A merges, those names are FROZEN, because packages D-I are
built against them at the same time. The tests here fail if anyone renames a
field or changes a type.

These tests never call a stub, so they stay valid after the bodies are written.

The last section checks the shared test helpers in tests/factories.py.
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
from factories import (
    NO_RESOURCES,
    make_advertisement,
    make_offer,
    make_protocol_error,
    make_readiness,
    make_result,
    make_state,
    make_transaction,
)
from generated import bazaar_pb2 as pb
from spec import SPEC_MAX_COMMAND_BYTES, SPEC_PROTOCOL_VERSION, TEST_PLACEHOLDER_VALUES

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


# --- tests/factories.py ---------------------------------------------------------

RUN_ID = TEST_PLACEHOLDER_VALUES["RUN_ID"]


def sample_offer():
    """Step 6's gift: P02 gives us one component and asks for nothing."""
    return make_offer(
        offer_id=TEST_PLACEHOLDER_VALUES["ZERO_PRICE_OFFER_ID"],
        proposer_id="P02",
        recipient_id="P01",
        give=Amounts(water=0, food=0, components=1),
        receive=NO_RESOURCES,
    )


def sample_advertisement():
    """Step 3's advertisement: sells nothing (an empty list), seeks components."""
    return make_advertisement(
        advertisement_id=TEST_PLACEHOLDER_VALUES["ADVERTISEMENT_ID"],
        station_id="P01",
        selling=(),
        seeking=(pb.RESOURCE_COMPONENTS,),
    )


def sample_transaction():
    """Step 5's trade: we paid two water for one food."""
    return make_transaction(
        transaction_id="test-transaction-1",
        offer_id="test-offer-1",
        proposer_id="P01",
        recipient_id="P02",
        give=Amounts(water=2, food=0, components=0),
        receive=Amounts(water=0, food=1, components=0),
    )


FACTORIES = (
    make_state,
    make_result,
    make_readiness,
    make_protocol_error,
    sample_offer,
    sample_advertisement,
    sample_transaction,
)


@pytest.mark.parametrize("factory", FACTORIES, ids=lambda factory: factory.__name__)
def test_factory_output_is_complete_and_survives_bytes(factory):
    message = factory()
    assert message.IsInitialized(), message.FindInitializationErrors()
    assert type(message).FromString(message.SerializeToString()) == message


@pytest.mark.parametrize("factory", FACTORIES, ids=lambda factory: factory.__name__)
def test_factory_returns_a_new_message_every_call(factory):
    first, second = factory(), factory()
    # Save the second one as bytes: if both calls returned the SAME object,
    # comparing it with itself after the Clear() would wrongly pass.
    untouched = second.SerializeToString()
    first.Clear()  # wipe out everything in the first one...
    assert second.SerializeToString() == untouched  # ...and the second is unchanged


@pytest.mark.parametrize(
    ("factory", "selected"),
    [
        (make_state, "state"),
        (make_result, "result"),
        (make_readiness, "readiness"),
        (make_protocol_error, "protocol_error"),
    ],
)
def test_factory_returns_the_outer_server_message(factory, selected):
    message = factory()
    assert isinstance(message, pb.ServerMessage)
    assert message.WhichOneof("message") == selected


def test_make_state_defaults_to_the_spec_step_1_state():
    state = make_state().state
    assert (state.run_id, state.protocol_version) == (RUN_ID, SPEC_PROTOCOL_VERSION)
    assert (state.world_version, state.snapshot_sequence) == (2, 1)
    assert (state.tick, state.phase) == (0, pb.PHASE_RUNNING)
    assert state.self_station_id == state.self.station_id == "P01"
    assert Amounts.from_bundle(state.self.inventory) == Amounts(30, 30, 30)
    assert state.self.specialty == pb.RESOURCE_WATER
    assert state.outcome.null is True  # the run hasn't ended


def test_make_state_defaults_to_p02_advertising_food_for_water():
    [advertisement] = make_state().state.advertisements.items
    assert advertisement.station_id == "P02"
    assert list(advertisement.selling.items) == [pb.RESOURCE_FOOD]
    assert list(advertisement.seeking.items) == [pb.RESOURCE_WATER]
    assert advertisement.status == pb.PUBLICATION_STATUS_ACTIVE


@pytest.mark.parametrize("name", ["offers", "transactions", "request_results"])
def test_make_state_defaults_to_empty_but_present_lists(name):
    state = make_state().state
    assert state.HasField(name)  # `offers {}` is present; a missing one is invalid
    assert len(getattr(state, name).items) == 0


@pytest.mark.parametrize(
    "name",
    ["last_production", "last_unmet_upkeep", "produced_total", "consumed_total",
     "unmet_total", "imported_total", "exported_total"],
)
def test_make_state_totals_start_at_zero(name):
    # Spec step 10: these stay zero "because no simulation tick occurred".
    assert Amounts.from_bundle(getattr(make_state().state.self, name)) == NO_RESOURCES


def test_make_state_counters_start_at_zero():
    observation = make_state().state.self
    assert observation.fully_supplied_ticks == observation.shortage_ticks == 0
    assert observation.current_shortage_streak == observation.longest_shortage_streak == 0
    assert observation.failed_once is False


def test_make_state_rules_use_the_two_limits_the_spec_gives():
    rules = make_state().state.rules
    assert rules.max_command_bytes == SPEC_MAX_COMMAND_BYTES
    assert rules.max_request_records_per_station == 5


def test_make_state_overrides_numbers_and_amounts():
    state = make_state(
        run_id="test-run-2",
        world_version=9,
        snapshot_sequence=9,
        inventory=Amounts(28, 31, 31),
        imported_total=Amounts(0, 1, 1),
        exported_total=Amounts(2, 0, 0),
    ).state
    assert (state.run_id, state.world_version, state.snapshot_sequence) == ("test-run-2", 9, 9)
    assert Amounts.from_bundle(state.self.inventory) == Amounts(28, 31, 31)
    assert Amounts.from_bundle(state.self.imported_total) == Amounts(0, 1, 1)
    assert Amounts.from_bundle(state.self.exported_total) == Amounts(2, 0, 0)


def test_make_state_overrides_the_lists():
    offer, advertisement = sample_offer(), sample_advertisement()
    transaction, result = sample_transaction(), make_result().result
    state = make_state(
        offers=[offer],
        advertisements=[advertisement],
        transactions=[transaction],
        request_results=[result],
    ).state
    assert list(state.offers.items) == [offer]
    assert list(state.advertisements.items) == [advertisement]
    assert list(state.transactions.items) == [transaction]
    assert list(state.request_results.items) == [result]


def test_make_state_can_have_no_advertisements():
    state = make_state(advertisements=()).state
    assert state.HasField("advertisements")
    assert len(state.advertisements.items) == 0


def test_make_state_keeps_its_own_copy_of_each_list_item():
    offer = sample_offer()
    message = make_state(offers=[offer])
    offer.status = pb.OFFER_STATUS_ACCEPTED  # change the original afterwards
    assert message.state.offers.items[0].status == pb.OFFER_STATUS_OPEN


def test_changing_one_state_never_changes_the_next():
    first = make_state()
    first.state.self.inventory.water = 0
    first.state.advertisements.items[0].station_id = "P99"
    second = make_state().state
    assert second.self.inventory.water == 30
    assert second.advertisements.items[0].station_id == "P02"


def test_make_result_ids_are_null_unless_given():
    result = make_result().result
    assert (result.request_id, result.ok, result.code) == (
        "student-advertise-1", True, pb.RESULT_CODE_OK,
    )
    for wrapper in (result.object_id, result.transaction_id, result.retry_after_tick):
        assert wrapper.null is True  # `{ null: true }`: no value exists


def test_make_result_sets_the_ids_it_is_given():
    result = make_result(
        request_id="student-accept-1",
        processed_version=8,
        object_id="test-gift-offer-1",
        transaction_id="test-transaction-2",
    ).result
    assert (result.request_id, result.processed_version) == ("student-accept-1", 8)
    assert result.object_id.value == "test-gift-offer-1"
    assert result.transaction_id.value == "test-transaction-2"


def test_make_readiness_defaults_to_step_1s_confirmation():
    readiness = make_readiness().readiness
    assert (readiness.run_id, readiness.ready, readiness.snapshot_sequence) == (RUN_ID, True, 1)


def test_make_readiness_keeps_a_false_ready():
    # In proto2 a required `false` must still be present to be valid.
    message = make_readiness(ready=False)
    assert message.IsInitialized()
    assert message.readiness.ready is False


def test_make_protocol_error_defaults_to_step_9s_error():
    error = make_protocol_error().protocol_error
    assert error.code == pb.CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED
    assert (error.run_id.value, error.request_id.value) == (RUN_ID, "student-advertise-2")
    assert error.close_session is False


def test_make_protocol_error_can_have_null_ids():
    message = make_protocol_error(
        run_id=None, request_id=None, code=pb.CONTROL_CODE_BAD_MESSAGE, close_session=True
    )
    assert message.IsInitialized()
    error = message.protocol_error
    assert error.run_id.null is True and error.request_id.null is True
    assert error.close_session is True
