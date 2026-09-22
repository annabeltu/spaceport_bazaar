"""
Checks on the shared test helpers in tests/factories.py.

Other packages' tests (state, engine, runner) build their server messages
with these factories, so the factories must be right: every message complete
and valid, make_state() equal to the spec's step-1 state, and every call
returning a NEW message that no other test can change.
"""
import pytest

from bazaar_client.models import Amounts
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
