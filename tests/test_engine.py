"""
Tests for engine.py (package H): the scripted decision-maker that plays the
spec's 10-step practice exercise.

The engine is pure logic, so each test hands it server messages built with
tests/factories.py and checks the move it returns. `feed()` below plays the
runner's part for one message: apply it to the client state, ask the engine,
and if the engine says Send, pass the message through guards.check() (the
same gate the real runner uses) and record it. So every Send in these tests
has also passed the pre-send checks.

This file holds the fake server's script and the happy-path, readiness and
reconnect tests. The Stop tests (every mismatch and every unexpected message
type) are in test_engine_stops.py, which imports the helpers from here.
"""
from types import MappingProxyType

import pytest

from bazaar_client import engine, guards, state
from bazaar_client.engine import Finish, Send, Stop, Wait
from bazaar_client.models import Amounts
from factories import (
    make_advertisement,
    make_offer,
    make_protocol_error,
    make_readiness,
    make_result,
    make_state,
    make_transaction,
)
from generated import bazaar_pb2 as pb
from spec import TEST_PLACEHOLDER_VALUES, load_spec_message

WATER, FOOD, COMPONENTS = pb.RESOURCE_WATER, pb.RESOURCE_FOOD, pb.RESOURCE_COMPONENTS
OPEN, ACCEPTED = pb.OFFER_STATUS_OPEN, pb.OFFER_STATUS_ACCEPTED

# --- IDs the fake server hands out ------------------------------------------------

# The server names every object. In these tests we play the server, so we pick
# the names. The spec's answer keys use three placeholders (RUN_ID,
# ADVERTISEMENT_ID, ZERO_PRICE_OFFER_ID); the happy path takes them from one of
# these mappings, so the engine's messages can be compared byte for byte with
# the answer keys filled in with the same values.
ANSWER_KEY_IDS = TEST_PLACEHOLDER_VALUES
# A second set, to prove the engine copies IDs from the server's messages
# instead of having the answer keys' values written into it.
OTHER_SERVER_IDS = MappingProxyType(
    {
        "RUN_ID": "run-7f3a",
        "ADVERTISEMENT_ID": "ad-000042",
        "ZERO_PRICE_OFFER_ID": "offer-000099",
    }
)

# IDs no answer key contains, so any value works.
P02_AD_ID = "test-p02-advertisement-1"
FIRST_AD_ID = "test-advertisement-0"  # step 2's ad, before step 3 replaces it
OUR_OFFER_ID = "test-offer-1"
TRADE_ID = "test-transaction-1"  # step 5: P02 accepts our offer
GIFT_TRADE_ID = "test-transaction-2"  # step 7: we accept the gift

# What each happy-path message should make the engine do next: an answer
# key's filename means "send exactly this".
WAIT = "wait"
FINISH = "finish"


# --- List items for the states ----------------------------------------------------


def p02_ad():
    """Step 1: "P02 has an advertisement selling food and seeking water.\""""
    return make_advertisement(
        advertisement_id=P02_AD_ID, station_id="P02", selling=(FOOD,), seeking=(WATER,)
    )


def water_for_food_ad():
    """Our step 2 advertisement."""
    return make_advertisement(
        advertisement_id=FIRST_AD_ID, station_id="P01", selling=(WATER,), seeking=(FOOD,)
    )


def components_ad(ids=ANSWER_KEY_IDS, advertisement_id=None):
    """Our step 3 advertisement: sells nothing, seeks components."""
    return make_advertisement(
        advertisement_id=advertisement_id or ids["ADVERTISEMENT_ID"],
        station_id="P01",
        selling=(),
        seeking=(COMPONENTS,),
    )


def our_offer(status):
    """Our step 4 offer: two water for one food. Once P02 accepts it (step 5)
    it's closed at tick 0 and names the trade."""
    accepted = status == ACCEPTED
    return make_offer(
        offer_id=OUR_OFFER_ID,
        proposer_id="P01",
        recipient_id="P02",
        give=Amounts(water=2, food=0, components=0),
        receive=Amounts(water=0, food=1, components=0),
        status=status,
        closed_tick=0 if accepted else None,
        transaction_id=TRADE_ID if accepted else None,
    )


def gift_offer(ids=ANSWER_KEY_IDS, status=OPEN, offer_id=None, give=None):
    """P02's step 6 gift: one component for nothing."""
    accepted = status == ACCEPTED
    return make_offer(
        offer_id=offer_id or ids["ZERO_PRICE_OFFER_ID"],
        proposer_id="P02",
        recipient_id="P01",
        give=give or Amounts(water=0, food=0, components=1),
        receive=Amounts(water=0, food=0, components=0),
        status=status,
        closed_tick=0 if accepted else None,
        transaction_id=GIFT_TRADE_ID if accepted else None,
    )


def trade():
    """Step 5's transaction: P02 accepted our offer."""
    return make_transaction(
        transaction_id=TRADE_ID,
        offer_id=OUR_OFFER_ID,
        proposer_id="P01",
        recipient_id="P02",
        give=Amounts(water=2, food=0, components=0),
        receive=Amounts(water=0, food=1, components=0),
    )


def gift_trade(ids=ANSWER_KEY_IDS):
    """Step 7's transaction: we accepted the gift."""
    return make_transaction(
        transaction_id=GIFT_TRADE_ID,
        offer_id=ids["ZERO_PRICE_OFFER_ID"],
        proposer_id="P02",
        recipient_id="P01",
        give=Amounts(water=0, food=0, components=1),
        receive=Amounts(water=0, food=0, components=0),
    )


# --- The spec's server messages, step by step --------------------------------------


def spec_result(step, ids=ANSWER_KEY_IDS, **changes):
    """The successful `result` the spec describes for `step`, with `changes`
    (make_result arguments) applied on top."""
    by_step = {
        2: dict(request_id="student-advertise-1", processed_version=3),
        3: dict(
            request_id="student-advertise-seeking-1",
            processed_version=4,
            object_id=ids["ADVERTISEMENT_ID"],  # "Save result.object_id.value"
        ),
        4: dict(request_id="student-offer-1", processed_version=5, object_id=OUR_OFFER_ID),
        7: dict(
            request_id="student-accept-1",
            processed_version=8,
            # "The result identifies the accepted offer and its transaction."
            object_id=ids["ZERO_PRICE_OFFER_ID"],
            transaction_id=GIFT_TRADE_ID,
        ),
        8: dict(request_id="student-withdraw-1", processed_version=9),
    }
    return make_result(**{"run_id": ids["RUN_ID"], **by_step[step], **changes})


def stored_results(ids=ANSWER_KEY_IDS):
    """Step 10: `request_results.items` holds the five stored results."""
    return [spec_result(step, ids).result for step in (2, 3, 4, 7, 8)]


def spec_state(step, ids=ANSWER_KEY_IDS, **changes):
    """The `state` the spec describes for `step` (1-8 or 10), with `changes`
    (make_state arguments) applied on top. make_state()'s defaults are
    step 1's values."""
    # After step 5's trade: paid 2 water, got 1 food. After step 7: +1 component.
    traded = dict(
        inventory=Amounts(water=28, food=31, components=30),
        imported_total=Amounts(water=0, food=1, components=0),
        exported_total=Amounts(water=2, food=0, components=0),
    )
    gifted = dict(
        traded,
        inventory=Amounts(water=28, food=31, components=31),
        imported_total=Amounts(water=0, food=1, components=1),
    )
    listed = [p02_ad(), components_ad(ids)]  # steps 3-7
    both_accepted = [our_offer(ACCEPTED), gift_offer(ids, status=ACCEPTED)]  # steps 7-10
    by_step = {
        1: dict(),
        2: dict(world_version=3, snapshot_sequence=2, advertisements=[p02_ad(), water_for_food_ad()]),
        3: dict(world_version=4, snapshot_sequence=3, advertisements=listed),
        4: dict(world_version=5, snapshot_sequence=4, advertisements=listed, offers=[our_offer(OPEN)]),
        5: dict(
            traded, world_version=6, snapshot_sequence=5, advertisements=listed,
            offers=[our_offer(ACCEPTED)], transactions=[trade()],
        ),
        6: dict(
            traded, world_version=7, snapshot_sequence=6, advertisements=listed,
            offers=[our_offer(ACCEPTED), gift_offer(ids)], transactions=[trade()],
        ),
        7: dict(
            gifted, world_version=8, snapshot_sequence=7, advertisements=listed,
            offers=both_accepted, transactions=[trade(), gift_trade(ids)],
        ),
        8: dict(
            gifted, world_version=9, snapshot_sequence=8, advertisements=[p02_ad()],
            offers=both_accepted, transactions=[trade(), gift_trade(ids)],
        ),
        10: dict(
            gifted, world_version=9, snapshot_sequence=9, advertisements=[p02_ad()],
            offers=both_accepted, transactions=[trade(), gift_trade(ids)],
            request_results=stored_results(ids),
        ),
    }
    return make_state(**{"run_id": ids["RUN_ID"], **by_step[step], **changes})


def with_field(message, dotted_path, value):
    """`message` with one field of its state set, e.g. "self.shortage_ticks".
    For the fields make_state() doesn't take as arguments."""
    *parents, name = dotted_path.split(".")
    target = message.state
    for parent in parents:
        target = getattr(target, parent)
    setattr(target, name, value)
    return message


def happy_path(ids=ANSWER_KEY_IDS):
    """The spec's 16 server messages in order, each paired with the move the
    engine should make next."""
    return [
        (spec_state(1, ids), "01_ready.textproto"),
        (make_readiness(run_id=ids["RUN_ID"]), "02_advertise_water_for_food.textproto"),
        (spec_result(2, ids), WAIT),
        (spec_state(2, ids), "03_advertise_seeking_components.textproto"),
        (spec_result(3, ids), WAIT),
        (spec_state(3, ids), "04_offer_water_for_food.textproto"),
        # Steps 4-6: one result, then three states, with no command in between.
        (spec_result(4, ids), WAIT),
        (spec_state(4, ids), WAIT),
        (spec_state(5, ids), WAIT),
        (spec_state(6, ids), "07_accept_gift.textproto"),
        (spec_result(7, ids), WAIT),
        (spec_state(7, ids), "08_withdraw_advertisement.textproto"),
        (spec_result(8, ids), WAIT),
        (spec_state(8, ids), "09_advertise_over_request_limit.textproto"),
        (make_protocol_error(run_id=ids["RUN_ID"]), "10_sync.textproto"),
        (spec_state(10, ids), FINISH),
    ]


def messages_of(script):
    """Just the server messages from a list of (message, expected move) pairs."""
    return [message for message, _ in script]


# --- Playing the runner's part ---------------------------------------------------


def record_send(client, message):
    """What the runner does after the engine says Send: check the message,
    "send" it, and record it in the client state."""
    data = guards.check(message, state.guard_context(client))  # raises if unsafe
    which = message.WhichOneof("message")
    if which == "ready":
        return state.record_ready(client, message.ready.snapshot_sequence)
    if which == "sync":
        return client  # sync has no request_id, so there's nothing to record
    return state.record_sent(client, getattr(message, which).request_id, data)


def feed(client, position, message):
    """The runner's loop body for one server message. Returns the new client
    state, the new position and the engine's decision."""
    client = state.apply_server_message(client, message)
    position, decision = engine.decide(client, position, message)
    if isinstance(decision, Send):
        client = record_send(client, decision.message)
    return client, position, decision


def run(messages, client=None, position=None):
    """Feed `messages` in order, from the start unless a client and position
    are given. Returns (client, position, decisions)."""
    client = state.initial() if client is None else client
    position = engine.first_position() if position is None else position
    decisions = []
    for message in messages:
        client, position, decision = feed(client, position, message)
        decisions.append(decision)
    return client, position, decisions


def assert_move(decision, expected, ids=ANSWER_KEY_IDS, where=""):
    """Check one decision: WAIT, FINISH, or a Send that is byte-identical to
    the named answer key (filled in with the same IDs)."""
    if expected == WAIT:
        assert decision == Wait(), where
    elif expected == FINISH:
        assert isinstance(decision, Finish), f"{where}: {decision}"
    else:
        assert isinstance(decision, Send), f"{where}: expected to send {expected}, got {decision}"
        answer_key = load_spec_message(expected, ids)
        assert decision.message.SerializeToString() == answer_key.SerializeToString(), (
            f"{where}: sent\n{decision.message}\nbut {expected} is\n{answer_key}"
        )


def assert_moves(client, position, script, ids=ANSWER_KEY_IDS):
    """Feed a script of (message, expected move) pairs, checking every move.
    Returns the final client and position."""
    for number, (message, expected) in enumerate(script, start=1):
        client, position, decision = feed(client, position, message)
        assert_move(decision, expected, ids, where=f"message {number}")
    return client, position


# --- The whole script -------------------------------------------------------------


@pytest.mark.parametrize(
    "ids", [ANSWER_KEY_IDS, OTHER_SERVER_IDS], ids=["answer-key IDs", "other server IDs"]
)
def test_the_whole_script_sends_every_answer_key_byte_for_byte(ids):
    # Arrange
    script = happy_path(ids)

    # Act
    _, _, decisions = run(messages_of(script))

    # Assert: every move, in order, so the first wrong one is the one reported
    for number, ((_, expected), decision) in enumerate(zip(script, decisions), start=1):
        assert_move(decision, expected, ids, where=f"message {number}")
    # The spec's totals: "you have sent 8 messages and received 16 messages".
    assert len(decisions) == 16
    assert sum(isinstance(decision, Send) for decision in decisions) == 8


def test_the_ids_in_later_commands_are_the_ones_the_server_sent():
    # Every ID below comes from a server message; none is in the answer keys.
    _, position, _ = run(messages_of(happy_path(OTHER_SERVER_IDS)))

    assert position.run_id == "run-7f3a"
    assert position.advertisement_id == "ad-000042"  # step 3's result.object_id
    assert position.our_offer_id == OUR_OFFER_ID  # found in step 4's state
    assert position.zero_price_offer_id == "offer-000099"  # step 6's gift


def test_finish_summarises_the_final_checks():
    _, _, decisions = run(messages_of(happy_path()))

    assert "(28,31,31)" in decisions[-1].summary


def test_the_script_starts_at_step_1_with_nothing_saved():
    position = engine.first_position()

    assert position.step == 1
    assert position.run_id is None
    assert position.advertisement_id is None
    assert position.our_offer_id is None
    assert position.zero_price_offer_id is None


def test_decide_never_changes_what_it_was_given():
    client, position = state.initial(), engine.first_position()
    for message, _ in happy_path():
        client = state.apply_server_message(client, message)
        message_before = message.SerializeToString()
        snapshot_before = client.snapshot.SerializeToString()
        results_before = {key: value.SerializeToString() for key, value in client.results.items()}

        new_position, decision = engine.decide(client, position, message)

        assert message.SerializeToString() == message_before
        assert client.snapshot.SerializeToString() == snapshot_before
        assert {key: value.SerializeToString() for key, value in client.results.items()} == (
            results_before
        )
        if isinstance(decision, Send):
            client = record_send(client, decision.message)
        position = new_position


# --- Readiness and reconnects ------------------------------------------------------

# The runner calls state.on_new_connection() when it reconnects. The same
# running server keeps the run's progress and starts the new connection with
# its current state, sequence 1. Readiness must be declared again before any
# command or retry.


def test_reconnect_before_a_result_redeclares_readiness_then_retries_exactly():
    # Arrange: advertise-1 is sent, then the connection drops before its result.
    script = happy_path()
    client, position, _ = run(messages_of(script[:2]))
    first_bytes = client.sent_requests["student-advertise-1"]
    client = state.on_new_connection(client)

    # Act / Assert: the new connection's state (sequence 1) gets a new `ready`
    # with that sequence. It isn't one of step 2's replies, so it isn't
    # checked against step 2's values.
    client, position, decision = feed(client, position, spec_state(1))
    assert_move(decision, "01_ready.textproto")

    # Once readiness is confirmed: the same command with the same request_id.
    client, position, decision = feed(client, position, make_readiness())
    assert_move(decision, "02_advertise_water_for_food.textproto")
    assert decision.message.SerializeToString() == first_bytes

    # The rest of the script carries on as normal. Step 2's state is sequence
    # 2 on the new connection too, so the spec's own messages still fit.
    assert_moves(client, position, script[2:])


def test_reconnect_while_waiting_for_states_uses_the_new_connections_states():
    # Arrange: step 4's result arrived, then the connection dropped before its state.
    client, position, _ = run(messages_of(happy_path()[:7]))
    client = state.on_new_connection(client)

    # Act / Assert
    assert_moves(client, position, [
        # The new connection's current state IS step 4's state: checked as
        # one, and then readiness is declared with its sequence (1).
        (spec_state(4, snapshot_sequence=1), "01_ready.textproto"),
        # P02 accepts before our readiness reply arrives. It's step 5's
        # state, so it's checked, but a ready is pending, so we just wait.
        (spec_state(5, snapshot_sequence=2), WAIT),
        # The reply echoes the sequence our ready carried (1), not the latest
        # state's (2). Step 6's state is next and nothing needs resending.
        (make_readiness(snapshot_sequence=1), WAIT),
        (spec_state(6, snapshot_sequence=3), "07_accept_gift.textproto"),
    ])


def test_reconnect_while_waiting_for_step_9s_error_syncs_instead_of_retrying():
    # Arrange: student-advertise-2 is sent, then the connection drops.
    client, position, _ = run(messages_of(happy_path()[:14]))
    client = state.on_new_connection(client)

    # Act / Assert: the spec says step 9 has no stored result, so "continue
    # to sync instead of retrying that command".
    assert_moves(client, position, [
        (spec_state(8, snapshot_sequence=1), "01_ready.textproto"),
        (make_readiness(), "10_sync.textproto"),
        (spec_state(10, snapshot_sequence=2), FINISH),
    ])


def test_a_new_connection_must_start_with_a_state():
    client, position, _ = run(messages_of(happy_path()[:2]))
    client = state.on_new_connection(client)

    _, _, decision = feed(client, position, spec_result(2))

    assert isinstance(decision, Stop)
    assert "new connection" in decision.reason


def test_a_new_connections_first_state_must_be_sequence_1():
    client, position, _ = run(messages_of(happy_path()[:2]))
    client = state.on_new_connection(client)

    _, _, decision = feed(client, position, spec_state(1, snapshot_sequence=3))

    assert isinstance(decision, Stop)
    assert "snapshot_sequence" in decision.reason
