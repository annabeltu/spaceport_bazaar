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
