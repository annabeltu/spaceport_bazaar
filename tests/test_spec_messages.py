"""
Checks on the answer keys: the spec's example messages.

These run before any client code exists. They prove the answer keys are
faithful to the spec and valid under our generated protobuf code -- so in
Phase 3, a failing test means the CLIENT is wrong, not the answer key.
"""
import pytest

from generated import bazaar_pb2 as pb
from spec import (
    FIXTURE_DIR,
    PLACEHOLDER_PATTERN,
    SPEC_MAX_COMMAND_BYTES,
    SPEC_MESSAGES,
    SPEC_PROTOCOL_VERSION,
    SPEC_REQUEST_ID_PATTERN,
    TEST_PLACEHOLDER_VALUES,
    extract_readme_examples,
    inner_command,
    load_spec_message,
    read_fixture_body,
)


def spec_id(spec):
    """Name each parametrized test case after its answer-key file."""
    return spec.filename


def has_request_id(spec):
    """Whether this command type has a request_id field at all."""
    command_type = pb.ClientMessage.DESCRIPTOR.fields_by_name[spec.command].message_type
    return "request_id" in command_type.fields_by_name


SPEC_MESSAGES_WITH_REQUEST_ID = tuple(s for s in SPEC_MESSAGES if has_request_id(s))


# --- The answer keys match the spec ------------------------------------------


def test_catalog_lists_every_answer_key_file_exactly_once():
    files_on_disk = sorted(path.name for path in FIXTURE_DIR.glob("*.textproto"))
    files_in_catalog = sorted(spec.filename for spec in SPEC_MESSAGES)
    assert files_in_catalog == files_on_disk


def test_answer_keys_are_copied_verbatim_from_the_spec_readme():
    # Catches a hand-edited answer key. (The README itself is protected by
    # its checksum, so if setup passed, the README is the original.)
    answer_keys = [read_fixture_body(spec.filename) for spec in SPEC_MESSAGES]
    assert answer_keys == extract_readme_examples()


# --- Each answer key is a valid message ---------------------------------------


@pytest.mark.parametrize("spec", SPEC_MESSAGES, ids=spec_id)
def test_answer_key_has_every_required_field(spec):
    message = load_spec_message(spec.filename)
    assert message.IsInitialized(), message.FindInitializationErrors()


@pytest.mark.parametrize("spec", SPEC_MESSAGES, ids=spec_id)
def test_answer_key_is_the_expected_command(spec):
    message = load_spec_message(spec.filename)
    assert message.WhichOneof("message") == spec.command


@pytest.mark.parametrize("spec", SPEC_MESSAGES, ids=spec_id)
def test_answer_key_survives_a_round_trip_through_bytes(spec):
    original = load_spec_message(spec.filename)
    wire_bytes = original.SerializeToString()
    decoded = pb.ClientMessage.FromString(wire_bytes)
    assert decoded == original
    assert decoded.SerializeToString() == wire_bytes


@pytest.mark.parametrize("spec", SPEC_MESSAGES, ids=spec_id)
def test_answer_key_fits_the_size_limit(spec):
    wire_bytes = load_spec_message(spec.filename).SerializeToString()
    assert len(wire_bytes) <= SPEC_MAX_COMMAND_BYTES


@pytest.mark.parametrize("spec", SPEC_MESSAGES, ids=spec_id)
def test_answer_key_uses_protocol_version_2_0(spec):
    command = inner_command(load_spec_message(spec.filename))
    assert command.protocol_version == SPEC_PROTOCOL_VERSION


@pytest.mark.parametrize("spec", SPEC_MESSAGES, ids=spec_id)
def test_answer_key_carries_the_run_id(spec):
    command = inner_command(load_spec_message(spec.filename))
    assert command.run_id == TEST_PLACEHOLDER_VALUES["RUN_ID"]


# --- Request IDs --------------------------------------------------------------


def test_only_ready_and_sync_have_no_request_id():
    commands_without_request_id = {
        spec.command for spec in SPEC_MESSAGES if not has_request_id(spec)
    }
    assert commands_without_request_id == {"ready", "sync"}


@pytest.mark.parametrize("spec", SPEC_MESSAGES_WITH_REQUEST_ID, ids=spec_id)
def test_request_id_follows_the_spec_format(spec):
    command = inner_command(load_spec_message(spec.filename))
    assert SPEC_REQUEST_ID_PATTERN.fullmatch(command.request_id)


def test_every_request_id_is_different():
    # The spec: a new command needs a new request_id. Reusing one means
    # "retry that exact command". Step 9 sends step 2's advertisement again,
    # but with a NEW id -- because it's a new command, not a retry.
    request_ids = [
        inner_command(load_spec_message(spec.filename)).request_id
        for spec in SPEC_MESSAGES_WITH_REQUEST_ID
    ]
    assert len(request_ids) == len(set(request_ids))


# --- The loader ---------------------------------------------------------------


def test_loader_refuses_to_leave_a_placeholder_unfilled():
    values_missing_the_gift_id = {"RUN_ID": "test-run-1"}
    with pytest.raises(ValueError, match="ZERO_PRICE_OFFER_ID"):
        load_spec_message("07_accept_gift.textproto", values_missing_the_gift_id)


@pytest.mark.parametrize("spec", SPEC_MESSAGES, ids=spec_id)
def test_loaded_message_has_no_leftover_placeholder(spec):
    message_text = str(load_spec_message(spec.filename))
    assert not PLACEHOLDER_PATTERN.search(message_text)
