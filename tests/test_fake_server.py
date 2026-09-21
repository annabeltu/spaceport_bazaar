"""Tests for the independent Spaceport Bazaar fake server."""

from generated import bazaar_pb2 as pb

from fake_server.scenario import Scenario
from spec import load_spec_message


def test_initial_state_matches_the_practice_spec():
    scenario = Scenario.create(run_id="fake-run-1")

    message = scenario.initial_message()

    assert message.IsInitialized()
    assert message.WhichOneof("message") == "state"
    assert message.state.run_id == "fake-run-1"
    assert message.state.world_version == 2
    assert message.state.snapshot_sequence == 1
    assert message.state.self_station_id == "P01"
    assert (
        message.state.self.inventory.water,
        message.state.self.inventory.food,
        message.state.self.inventory.components,
    ) == (30, 30, 30)
    assert message.state.self.specialty == pb.RESOURCE_WATER
    assert len(message.state.advertisements.items) == 1
    peer_advertisement = message.state.advertisements.items[0]
    assert peer_advertisement.station_id == "P02"
    assert list(peer_advertisement.selling.items) == [pb.RESOURCE_FOOD]
    assert list(peer_advertisement.seeking.items) == [pb.RESOURCE_WATER]


def test_readiness_echoes_the_connection_snapshot_without_advancing_world():
    scenario = Scenario.create(run_id="fake-run-1")
    ready = load_spec_message(
        "01_ready.textproto", placeholder_values={"RUN_ID": scenario.run_id}
    )

    updated, replies = scenario.handle(ready)

    assert updated.ready is True
    assert updated.world_version == 2
    assert updated.snapshot_sequence == 1
    assert len(replies) == 1
    assert replies[0].IsInitialized()
    assert replies[0].WhichOneof("message") == "readiness"
    assert replies[0].readiness.run_id == scenario.run_id
    assert replies[0].readiness.ready is True
    assert replies[0].readiness.snapshot_sequence == 1


def test_pure_scenario_plays_the_complete_exchange_from_answer_keys():
    scenario = Scenario.create(run_id="fake-run-1")
    received = [scenario.initial_message()]

    filenames = (
        "01_ready.textproto",
        "02_advertise_water_for_food.textproto",
        "03_advertise_seeking_components.textproto",
        "04_offer_water_for_food.textproto",
        "07_accept_gift.textproto",
        "08_withdraw_advertisement.textproto",
        "09_advertise_over_request_limit.textproto",
        "10_sync.textproto",
    )
    placeholders = {"RUN_ID": scenario.run_id}

    sent = []
    for filename in filenames:
        message = load_spec_message(filename, placeholder_values=placeholders)
        sent.append(message)
        scenario, replies = scenario.handle(message)
        received.extend(replies)

        for reply in replies:
            assert reply.IsInitialized(), reply.FindInitializationErrors()

        if filename == "03_advertise_seeking_components.textproto":
            placeholders["ADVERTISEMENT_ID"] = replies[0].result.object_id.value
        if filename == "04_offer_water_for_food.textproto":
            gift_state = replies[-1].state
            gift = next(
                offer
                for offer in gift_state.offers.items
                if offer.proposer_id == "P02" and offer.status == pb.OFFER_STATUS_OPEN
            )
            placeholders["ZERO_PRICE_OFFER_ID"] = gift.offer_id

    assert len(sent) == 8
    assert len(received) == 16

    states = [message.state for message in received if message.HasField("state")]
    assert [state.world_version for state in states] == [2, 3, 4, 5, 6, 7, 8, 9, 9]
    assert [state.snapshot_sequence for state in states] == list(range(1, 10))

    final_state = states[-1]
    assert (
        final_state.self.inventory.water,
        final_state.self.inventory.food,
        final_state.self.inventory.components,
    ) == (28, 31, 31)
    assert len(final_state.transactions.items) == 2
    assert len(final_state.request_results.items) == 5
    assert (
        final_state.self.imported_total.water,
        final_state.self.imported_total.food,
        final_state.self.imported_total.components,
    ) == (0, 1, 1)
    assert (
        final_state.self.exported_total.water,
        final_state.self.exported_total.food,
        final_state.self.exported_total.components,
    ) == (2, 0, 0)

    capacity_error = received[-2].protocol_error
    assert capacity_error.code == pb.CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED
    assert capacity_error.close_session is False
    assert capacity_error.request_id.value == "student-advertise-2"
