"""
Stop tests for engine.py (package H): every way a server message can differ
from the spec makes the engine return Stop with a clear reason, never a guess.

Each case walks the spec's happy path (from test_engine.py) up to one message,
feeds a changed message in its place, and checks the Stop's reason names what
was wrong. The number in each case is the position of the replaced message in
the happy path, counting from 0:

     0 step 1 state      4 step 3 result     8 step 5 state     12 step 8 result
     1 readiness         5 step 3 state      9 step 6 state     13 step 8 state
     2 step 2 result     6 step 4 result    10 step 7 result    14 step 9 error
     3 step 2 state      7 step 4 state     11 step 7 state     15 step 10 state
"""
import pytest

from bazaar_client.engine import Stop
from bazaar_client.models import Amounts
from factories import make_protocol_error, make_readiness
from generated import bazaar_pb2 as pb
from test_engine import (
    ACCEPTED,
    OPEN,
    OUR_OFFER_ID,
    components_ad,
    feed,
    gift_offer,
    happy_path,
    messages_of,
    our_offer,
    p02_ad,
    run,
    spec_result,
    spec_state,
    stored_results,
    trade,
    water_for_food_ad,
    with_field,
)

OTHER_RUN_ID = "test-run-2"


def decision_instead_of(index, bad_message):
    """Walk the happy path up to message `index`, then feed `bad_message` in
    its place. Returns the engine's decision."""
    client, position, _ = run(messages_of(happy_path()[:index]))
    _, _, decision = feed(client, position, bad_message)
    return decision


# (where, the message fed instead, words the Stop reason must contain)
MISMATCHES = [
    # Step 1: "world_version: 2 and snapshot_sequence: 1 ... P01, inventory
    # (30,30,30), self.specialty RESOURCE_WATER, P02 has an advertisement
    # selling food and seeking water." Plus "every state in this exercise has
    # tick: 0 and phase: PHASE_RUNNING".
    pytest.param(0, spec_state(1, world_version=3), "world_version", id="1 world_version"),
    pytest.param(0, spec_state(1, snapshot_sequence=2), "snapshot_sequence", id="1 sequence"),
    pytest.param(0, spec_state(1, inventory=Amounts(30, 30, 29)), "inventory", id="1 inventory"),
    pytest.param(0, spec_state(1, advertisements=()), "P02", id="1 no P02 ad"),
    pytest.param(
        0, spec_state(1, advertisements=[water_for_food_ad()]), "P02", id="1 ad from P01 not P02"
    ),
    pytest.param(
        0, with_field(spec_state(1), "self.specialty", pb.RESOURCE_FOOD), "specialty",
        id="1 specialty",
    ),
    pytest.param(
        0, with_field(spec_state(1), "self_station_id", "P02"), "self_station_id", id="1 station"
    ),
    pytest.param(0, with_field(spec_state(1), "tick", 1), "tick", id="1 tick"),
    pytest.param(
        0, with_field(spec_state(1), "phase", pb.PHASE_PAUSED), "PHASE_PAUSED", id="1 phase"
    ),
    # Step 1's readiness must match our ready: run_id, ready true, sequence 1.
    pytest.param(1, make_readiness(ready=False), "readiness", id="1 ready false"),
    pytest.param(1, make_readiness(snapshot_sequence=2), "readiness", id="1 ready sequence"),
    pytest.param(1, make_readiness(run_id=OTHER_RUN_ID), "readiness", id="1 ready run"),
    # Every result: our request_id, ok true, RESULT_CODE_OK, and our run.
    pytest.param(
        2, spec_result(2, ok=False, code=pb.RESULT_CODE_INVALID_ARGUMENT),
        "RESULT_CODE_INVALID_ARGUMENT", id="2 result failed",
    ),
    pytest.param(
        2, spec_result(2, code=pb.RESULT_CODE_LIMIT_REACHED), "RESULT_CODE_LIMIT_REACHED",
        id="2 result ok but wrong code",
    ),
    pytest.param(
        2, spec_result(2, request_id="student-advertise-seeking-1"),
        "student-advertise-seeking-1", id="2 result other request",
    ),
    pytest.param(
        2, spec_result(2, run_id=OTHER_RUN_ID), "different run", id="2 result other run"
    ),
    # Step 2's state: our advertisement appears, and inventory is unchanged.
    pytest.param(3, spec_state(2, advertisements=[p02_ad()]), "active advertisement", id="2 no ad"),
    pytest.param(3, spec_state(2, inventory=Amounts(29, 30, 30)), "inventory", id="2 inventory"),
    pytest.param(3, spec_state(2, world_version=4), "world_version", id="2 world_version"),
    pytest.param(3, spec_state(2, snapshot_sequence=3), "snapshot_sequence", id="2 sequence"),
    pytest.param(
        3, spec_state(2, run_id=OTHER_RUN_ID), "different run", id="2 state from another run"
    ),
    # Step 3: save result.object_id; the new ad replaces the first one.
    pytest.param(4, spec_result(3, object_id=None), "object_id", id="3 result without object_id"),
    pytest.param(
        5, spec_state(3, advertisements=[p02_ad(), water_for_food_ad(), components_ad()]),
        "found 2", id="3 first ad not replaced",
    ),
    pytest.param(
        5, spec_state(3, advertisements=[p02_ad(), components_ad(advertisement_id="other-ad")]),
        "test-advertisement-1", id="3 ad is not the one the result named",
    ),
    pytest.param(
        5, spec_state(3, advertisements=[p02_ad(), water_for_food_ad()]), "seeks",
        id="3 ad still sells water",
    ),
    # Step 4: our offer to P02 is open, inventory still (30,30,30).
    pytest.param(7, spec_state(4, offers=[]), "offer to P02", id="4 no offer"),
    pytest.param(
        7, spec_state(4, offers=[our_offer(ACCEPTED)]), "OFFER_STATUS_ACCEPTED", id="4 not open"
    ),
    pytest.param(7, spec_state(4, inventory=Amounts(28, 30, 30)), "inventory", id="4 inventory"),
    # Step 5: our offer accepted, one transaction, inventory (28,31,30).
    pytest.param(
        8, spec_state(5, offers=[our_offer(OPEN)]), "OFFER_STATUS_OPEN", id="5 still open"
    ),
    pytest.param(8, spec_state(5, offers=[]), OUR_OFFER_ID, id="5 offer gone"),
    pytest.param(8, spec_state(5, transactions=[]), "transactions", id="5 no transaction"),
    pytest.param(8, spec_state(5, inventory=Amounts(30, 30, 30)), "inventory", id="5 inventory"),
    # Step 6: exactly one open offer from P02 to P01 with receive all zeros,
    # giving one component.
    pytest.param(9, spec_state(6, offers=[our_offer(ACCEPTED)]), "found 0", id="6 no gift"),
    pytest.param(
        9, spec_state(6, offers=[gift_offer(), gift_offer(offer_id="test-gift-offer-2")]),
        "found 2", id="6 two gifts",
    ),
    pytest.param(
        9, spec_state(6, offers=[gift_offer(give=Amounts(0, 0, 2))]), "(0,0,2)",
        id="6 gift gives two components",
    ),
    pytest.param(
        9, spec_state(6, offers=[gift_offer(status=ACCEPTED)]), "found 0", id="6 gift not open"
    ),
    # Step 7: the result names the gift and a transaction; two transactions,
    # inventory (28,31,31), and our advertisement is still active.
    pytest.param(
        10, spec_result(7, object_id=OUR_OFFER_ID), "not the gift", id="7 result names other offer"
    ),
    pytest.param(
        10, spec_result(7, transaction_id=None), "transaction_id", id="7 no transaction_id"
    ),
    pytest.param(
        11, spec_state(7, advertisements=[p02_ad()]), "active advertisement", id="7 ad gone"
    ),
    pytest.param(11, spec_state(7, transactions=[trade()]), "transactions", id="7 one transaction"),
    pytest.param(11, spec_state(7, inventory=Amounts(28, 31, 30)), "inventory", id="7 inventory"),
    # Step 8: our advertisement is absent.
    pytest.param(
        13, spec_state(8, advertisements=[p02_ad(), components_ad()]), "still", id="8 ad listed"
    ),
    # Step 9: only REQUEST_CAPACITY_EXCEEDED for student-advertise-2, with
    # close_session false, is expected.
    pytest.param(
        14, make_protocol_error(code=pb.CONTROL_CODE_BAD_MESSAGE), "CONTROL_CODE_BAD_MESSAGE",
        id="9 other code",
    ),
    pytest.param(
        14, make_protocol_error(close_session=True), "close_session=True", id="9 closes session"
    ),
    pytest.param(
        14, make_protocol_error(request_id="student-advertise-1"), "'student-advertise-1'",
        id="9 other request",
    ),
    pytest.param(14, make_protocol_error(request_id=None), "request_id=None", id="9 no request"),
    # Step 10's final checks.
    pytest.param(15, spec_state(10, world_version=10), "world_version", id="10 world_version"),
    pytest.param(
        15, spec_state(10, inventory=Amounts(28, 31, 30)), "inventory", id="10 inventory"
    ),
    pytest.param(15, spec_state(10, transactions=[trade()]), "transactions", id="10 transactions"),
    pytest.param(
        15, spec_state(10, request_results=stored_results()[:4]), "request_results",
        id="10 four stored results",
    ),
    pytest.param(
        15, spec_state(10, imported_total=Amounts(0, 1, 0)), "imported_total", id="10 imported"
    ),
    pytest.param(
        15, spec_state(10, exported_total=Amounts(2, 0, 1)), "exported_total", id="10 exported"
    ),
    pytest.param(
        15, with_field(spec_state(10), "self.produced_total.water", 1), "produced_total",
        id="10 production",
    ),
    pytest.param(
        15, with_field(spec_state(10), "self.consumed_total.food", 1), "consumed_total",
        id="10 consumption",
    ),
    pytest.param(
        15, with_field(spec_state(10), "self.shortage_ticks", 1), "shortage_ticks",
        id="10 shortage",
    ),
]


@pytest.mark.parametrize(("index", "bad_message", "words"), MISMATCHES)
def test_a_value_that_differs_from_the_spec_stops_the_script(index, bad_message, words):
    decision = decision_instead_of(index, bad_message)

    assert isinstance(decision, Stop), decision
    assert words in decision.reason


# (where, the message fed instead, words the Stop reason must contain)
UNEXPECTED = [
    # A connection must start with a state.
    pytest.param(0, spec_result(2), "result", id="result first"),
    pytest.param(0, make_readiness(), "readiness", id="readiness first"),
    pytest.param(0, make_protocol_error(), "protocol_error", id="protocol_error first"),
    pytest.param(0, pb.ServerMessage(), "nothing selected", id="empty message first"),
    # Replies in the wrong order, or twice.
    pytest.param(2, spec_state(2), "expected a result", id="2 state before its result"),
    pytest.param(3, spec_result(2), "expected a state", id="2 result twice"),
    pytest.param(2, make_readiness(), "no ready", id="second readiness"),
    pytest.param(8, spec_result(4), "expected a state", id="result between steps 4-6"),
    # "Any other protocol_error means Stop", even step 9's error if it's early.
    pytest.param(
        7, make_protocol_error(code=pb.CONTROL_CODE_BAD_MESSAGE, request_id=None),
        "CONTROL_CODE_BAD_MESSAGE", id="protocol_error mid-script",
    ),
    pytest.param(6, make_protocol_error(), "expected a result", id="step 9's error too early"),
    pytest.param(14, spec_result(8), "expected a protocol_error", id="9 result instead"),
    pytest.param(14, spec_state(10), "expected a protocol_error", id="9 state instead"),
    pytest.param(15, make_protocol_error(), "expected a state", id="10 error instead of state"),
    pytest.param(3, pb.ServerMessage(), "nothing selected", id="empty message mid-script"),
    # Anything after the script finished.
    pytest.param(
        16, spec_state(10, snapshot_sequence=10), "already finished", id="after finish"
    ),
]


@pytest.mark.parametrize(("index", "bad_message", "words"), UNEXPECTED)
def test_an_unexpected_message_type_stops_the_script(index, bad_message, words):
    decision = decision_instead_of(index, bad_message)

    assert isinstance(decision, Stop), decision
    assert words in decision.reason


def test_a_stop_keeps_the_position_it_was_given():
    # Nothing after a Stop is acted on, so the position must not move on.
    client, position, _ = run(messages_of(happy_path()[:3]))

    _, new_position, decision = feed(client, position, spec_state(2, world_version=99))

    assert isinstance(decision, Stop)
    assert new_position == position
