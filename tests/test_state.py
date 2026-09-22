"""
Tests for state.py: the client's view of the world (package F).

Every test builds its server messages with tests/factories.py (never by hand),
and checks two things over and over because they're the whole point of this
module: state.py never mutates the ClientState (or protobuf message) it was
given, and every function returns a brand NEW ClientState.
"""
from types import MappingProxyType

import pytest

from bazaar_client import errors, state
from bazaar_client.models import Amounts, ClientState, GuardContext
from factories import TEST_RUN_ID, make_protocol_error, make_readiness, make_result, make_state
from spec import SPEC_MAX_COMMAND_BYTES

# A second run ID, for tests where the server restarts. Any string that isn't
# TEST_RUN_ID works, since state.py only checks whether the two differ.
OTHER_RUN_ID = "test-run-2"


# --- initial() ----------------------------------------------------------------


def test_initial_state_has_nothing_yet():
    # Arrange / Act: initial() takes no arguments, so there's nothing to arrange.
    client = state.initial()

    # Assert
    assert client.run_id is None
    assert client.snapshot is None
    assert client.is_ready is False
    assert client.sent_ready_sequence is None
    assert dict(client.results) == {}
    assert dict(client.sent_requests) == {}


def test_initial_mappings_are_read_only():
    client = state.initial()

    # MappingProxyType is the read-only wrapper the module docstring in
    # models.py asks for. Checking the type (not just that writes fail) makes
    # sure we didn't just happen to build an empty dict that no one wrote to.
    assert isinstance(client.results, MappingProxyType)
    assert isinstance(client.sent_requests, MappingProxyType)


# --- apply_server_message: state ------------------------------------------------


def test_first_state_becomes_the_run_id_and_snapshot():
    client = state.initial()

    updated = state.apply_server_message(client, make_state())

    assert updated.run_id == TEST_RUN_ID
    assert updated.snapshot.snapshot_sequence == 1


def test_a_new_state_replaces_the_old_snapshot_instead_of_merging():
    # From the plan: the step 4 -> step 5 transition. Step 5's inventory
    # already includes step 4's trade, so applying both states in a row must
    # leave the LATER inventory, not double-count it.
    step_4 = make_state(snapshot_sequence=4, inventory=Amounts(water=28, food=31, components=31))
    step_5 = make_state(snapshot_sequence=5, inventory=Amounts(water=28, food=31, components=30))

    client = state.initial()
    client = state.apply_server_message(client, step_4)
    client = state.apply_server_message(client, step_5)

    inventory = client.snapshot.self.inventory
    assert (inventory.water, inventory.food, inventory.components) == (28, 31, 30)


def test_the_stored_snapshot_is_a_copy():
    client = state.initial()
    message = make_state()

    updated = state.apply_server_message(client, message)
    message.state.snapshot_sequence = 999  # mutate the original AFTER storing it

    assert updated.snapshot.snapshot_sequence == 1


def test_apply_server_message_with_a_state_never_changes_the_client_it_was_given():
    client = state.initial()

    state.apply_server_message(client, make_state())

    # `client` must still be exactly the untouched result of initial().
    assert client.run_id is None
    assert client.snapshot is None


def test_a_state_with_a_different_run_id_discards_old_results_and_requests():
    # Arrange: build up history from a first run: a result, a sent command,
    # and confirmed readiness.
    client = state.initial()
    client = state.apply_server_message(client, make_state())
    client = state.apply_server_message(client, make_result(request_id="student-advertise-1"))
    client = state.record_sent(client, "student-advertise-1", b"exact-bytes")
    client = state.record_ready(client, snapshot_sequence=1)
    client = state.apply_server_message(
        client, make_readiness(run_id=TEST_RUN_ID, ready=True, snapshot_sequence=1)
    )
    assert client.is_ready is True  # sanity check before the restart happens

    # Act: the server restarts with a brand new run.
    restarted_state = make_state(run_id=OTHER_RUN_ID, snapshot_sequence=1)
    updated = state.apply_server_message(client, restarted_state)

    # Assert: the old run's IDs mean nothing anymore, so their history is gone.
    assert updated.run_id == OTHER_RUN_ID
    assert dict(updated.results) == {}
    assert dict(updated.sent_requests) == {}
    assert updated.is_ready is False
    assert updated.sent_ready_sequence is None


# --- apply_server_message: result -----------------------------------------------


def test_result_is_stored_by_request_id_as_a_copy():
    client = state.initial()
    message = make_result(request_id="student-advertise-1", object_id="ad-1")

    updated = state.apply_server_message(client, message)
    message.result.object_id.value = "mutated-after-storing"  # mutate the original

    assert updated.results["student-advertise-1"].object_id.value == "ad-1"


def test_multiple_results_accumulate_by_request_id():
    client = state.initial()
    client = state.apply_server_message(client, make_result(request_id="r1"))

    updated = state.apply_server_message(client, make_result(request_id="r2"))

    assert set(updated.results.keys()) == {"r1", "r2"}


def test_apply_server_message_with_a_result_never_changes_the_client_it_was_given():
    client = state.initial()

    state.apply_server_message(client, make_result(request_id="r1"))

    assert dict(client.results) == {}


# --- apply_server_message: readiness ---------------------------------------------


def _client_ready_to_check(snapshot_sequence=1):
    """A client that has read state and sent `ready` for it, but isn't
    confirmed ready yet. Shared setup for the readiness tests below."""
    client = state.initial()
    client = state.apply_server_message(client, make_state(snapshot_sequence=snapshot_sequence))
    return state.record_ready(client, snapshot_sequence=snapshot_sequence)


def test_readiness_confirms_is_ready_when_run_id_and_sequence_match():
    client = _client_ready_to_check()

    updated = state.apply_server_message(
        client, make_readiness(run_id=TEST_RUN_ID, ready=True, snapshot_sequence=1)
    )

    assert updated.is_ready is True


def test_readiness_with_ready_false_does_not_confirm():
    client = _client_ready_to_check()

    updated = state.apply_server_message(
        client, make_readiness(run_id=TEST_RUN_ID, ready=False, snapshot_sequence=1)
    )

    assert updated.is_ready is False


def test_readiness_with_the_wrong_run_id_does_not_confirm():
    client = _client_ready_to_check()

    updated = state.apply_server_message(
        client, make_readiness(run_id=OTHER_RUN_ID, ready=True, snapshot_sequence=1)
    )

    assert updated.is_ready is False


def test_readiness_must_match_the_sequence_we_sent_not_the_latest_state():
    # The spec says the reply must "match your message". A newer state can
    # arrive in between, so readiness has to be checked against
    # sent_ready_sequence, not whatever snapshot_sequence is now on file.
    client = _client_ready_to_check(snapshot_sequence=1)
    client = state.apply_server_message(client, make_state(snapshot_sequence=2))

    updated = state.apply_server_message(
        client, make_readiness(run_id=TEST_RUN_ID, ready=True, snapshot_sequence=1)
    )

    assert updated.is_ready is True


def test_readiness_with_the_wrong_sequence_does_not_confirm():
    client = _client_ready_to_check(snapshot_sequence=1)

    updated = state.apply_server_message(
        client, make_readiness(run_id=TEST_RUN_ID, ready=True, snapshot_sequence=2)
    )

    assert updated.is_ready is False


def test_readiness_before_any_ready_was_sent_does_not_confirm():
    client = state.initial()
    client = state.apply_server_message(client, make_state())
    # sent_ready_sequence is still None here: record_ready was never called.

    updated = state.apply_server_message(
        client, make_readiness(run_id=TEST_RUN_ID, ready=True, snapshot_sequence=1)
    )

    assert updated.is_ready is False


def test_apply_server_message_with_a_readiness_never_changes_the_client_it_was_given():
    client = _client_ready_to_check()

    state.apply_server_message(client, make_readiness(run_id=TEST_RUN_ID, ready=True, snapshot_sequence=1))

    assert client.is_ready is False


# --- apply_server_message: protocol_error -----------------------------------------


def test_protocol_error_changes_nothing():
    client = state.initial()
    client = state.apply_server_message(client, make_state())

    updated = state.apply_server_message(client, make_protocol_error())

    # Not just equal: the SAME object, because nothing about it changed.
    assert updated is client


# --- record_sent ----------------------------------------------------------------


def test_record_sent_stores_the_exact_bytes_by_request_id():
    client = state.initial()

    updated = state.record_sent(client, "student-advertise-1", b"exact-bytes")

    assert updated.sent_requests["student-advertise-1"] == b"exact-bytes"


def test_record_sent_keeps_previous_entries():
    client = state.record_sent(state.initial(), "r1", b"first")

    updated = state.record_sent(client, "r2", b"second")

    assert dict(updated.sent_requests) == {"r1": b"first", "r2": b"second"}


def test_record_sent_never_changes_the_client_it_was_given():
    client = state.initial()

    state.record_sent(client, "student-advertise-1", b"exact-bytes")

    assert dict(client.sent_requests) == {}


def test_sent_requests_mapping_is_read_only():
    client = state.record_sent(state.initial(), "r1", b"data")

    assert isinstance(client.sent_requests, MappingProxyType)


# --- record_ready -----------------------------------------------------------------


def test_record_ready_stores_the_sequence_but_does_not_set_is_ready():
    client = state.initial()

    updated = state.record_ready(client, snapshot_sequence=1)

    assert updated.sent_ready_sequence == 1
    assert updated.is_ready is False


def test_record_ready_never_changes_the_client_it_was_given():
    client = state.initial()

    state.record_ready(client, snapshot_sequence=1)

    assert client.sent_ready_sequence is None


# --- on_new_connection --------------------------------------------------------------


def test_on_new_connection_clears_readiness_but_keeps_the_runs_progress():
    client = _client_ready_to_check()
    client = state.apply_server_message(
        client, make_readiness(run_id=TEST_RUN_ID, ready=True, snapshot_sequence=1)
    )
    client = state.record_sent(client, "r1", b"data")

    updated = state.on_new_connection(client)

    assert updated.is_ready is False
    assert updated.sent_ready_sequence is None
    # The run itself didn't restart, so its progress survives the reconnect.
    assert updated.run_id == client.run_id
    assert updated.snapshot == client.snapshot
    assert dict(updated.sent_requests) == dict(client.sent_requests)


def test_on_new_connection_never_changes_the_client_it_was_given():
    client = _client_ready_to_check()

    state.on_new_connection(client)

    assert client.sent_ready_sequence == 1


# --- guard_context -----------------------------------------------------------------


def test_guard_context_without_a_snapshot_raises():
    client = state.initial()

    # Without a snapshot there's nothing to check max_command_bytes or
    # inventory against, so nothing can be sent yet.
    with pytest.raises(errors.GuardError):
        state.guard_context(client)


def test_guard_context_reads_limits_and_inventory_from_the_latest_snapshot():
    client = state.initial()
    client = state.apply_server_message(
        client, make_state(inventory=Amounts(water=28, food=31, components=30))
    )
    client = state.record_sent(client, "r1", b"data")

    context = state.guard_context(client)

    assert isinstance(context, GuardContext)
    assert context.run_id == TEST_RUN_ID
    assert context.is_ready is False
    assert context.max_command_bytes == SPEC_MAX_COMMAND_BYTES
    assert context.inventory == Amounts(water=28, food=31, components=30)
    assert dict(context.sent_requests) == {"r1": b"data"}


def test_guard_context_reflects_confirmed_readiness():
    client = _client_ready_to_check()
    client = state.apply_server_message(
        client, make_readiness(run_id=TEST_RUN_ID, ready=True, snapshot_sequence=1)
    )

    context = state.guard_context(client)

    assert context.is_ready is True
