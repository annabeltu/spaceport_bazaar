"""Tests for the independent Spaceport Bazaar fake server."""

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from generated import bazaar_pb2 as pb

from fake_server.scenario import Scenario
from fake_server.server import FakeServer
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


@pytest.mark.asyncio
async def test_real_socket_plays_the_complete_exchange():
    async with FakeServer() as server:
        async with connect(
            server.url,
            additional_headers={"Authorization": f"Bearer {server.token}"},
            subprotocols=["bazaar.protobuf.v2"],
        ) as websocket:
            first = pb.ServerMessage.FromString(await websocket.recv())
            assert first.state.run_id == server.run_id

            placeholders = {"RUN_ID": server.run_id}
            filenames_and_reply_counts = (
                ("01_ready.textproto", 1),
                ("02_advertise_water_for_food.textproto", 2),
                ("03_advertise_seeking_components.textproto", 2),
                ("04_offer_water_for_food.textproto", 4),
                ("07_accept_gift.textproto", 2),
                ("08_withdraw_advertisement.textproto", 2),
                ("09_advertise_over_request_limit.textproto", 1),
                ("10_sync.textproto", 1),
            )
            received = [first]

            for filename, reply_count in filenames_and_reply_counts:
                outgoing = load_spec_message(filename, placeholder_values=placeholders)
                await websocket.send(outgoing.SerializeToString())
                replies = [
                    pb.ServerMessage.FromString(await websocket.recv())
                    for _ in range(reply_count)
                ]
                received.extend(replies)
                if filename == "03_advertise_seeking_components.textproto":
                    placeholders["ADVERTISEMENT_ID"] = replies[0].result.object_id.value
                if filename == "04_offer_water_for_food.textproto":
                    gift = next(
                        offer
                        for offer in replies[-1].state.offers.items
                        if offer.proposer_id == "P02"
                        and offer.status == pb.OFFER_STATUS_OPEN
                    )
                    placeholders["ZERO_PRICE_OFFER_ID"] = gift.offer_id

    assert len(server.received) == 8
    assert len(server.sent) == 16
    assert len(received) == 16
    assert received[-1].state.self.inventory.water == 28
    assert received[-1].state.self.inventory.food == 31
    assert received[-1].state.self.inventory.components == 31
    assert all(message.IsInitialized() for message in server.sent)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "subprotocols", "status_code"),
    (
        ({"Authorization": "Bearer wrong"}, ["bazaar.protobuf.v2"], 401),
        ({}, ["bazaar.protobuf.v2"], 401),
        ("p02", ["bazaar.protobuf.v2"], 400),
        ("valid", None, 400),
        ("valid", ["wrong.protocol"], 400),
    ),
)
async def test_handshake_rejects_bad_auth_station_or_subprotocol(
    headers, subprotocols, status_code
):
    async with FakeServer() as server:
        if headers == "p02":
            headers = {"Authorization": f"Bearer {server.p02_token}"}
        elif headers == "valid":
            headers = {"Authorization": f"Bearer {server.token}"}

        with pytest.raises(InvalidStatus) as caught:
            async with connect(
                server.url,
                additional_headers=headers,
                subprotocols=subprotocols,
            ):
                pass

    assert caught.value.response.status_code == status_code


@pytest.mark.asyncio
@pytest.mark.parametrize("frame", (b"not protobuf", b"", "text frame"))
async def test_bad_frames_receive_a_nonclosing_bad_message(frame):
    async with FakeServer() as server:
        async with connect(
            server.url,
            additional_headers={"Authorization": f"Bearer {server.token}"},
            subprotocols=["bazaar.protobuf.v2"],
        ) as websocket:
            await websocket.recv()
            await websocket.send(frame)
            reply = pb.ServerMessage.FromString(await websocket.recv())

            assert reply.protocol_error.code == pb.CONTROL_CODE_BAD_MESSAGE
            assert reply.protocol_error.close_session is False
            assert websocket.state.name == "OPEN"


@pytest.mark.asyncio
async def test_trading_before_readiness_is_rejected_but_connection_stays_open():
    async with FakeServer() as server:
        async with connect(
            server.url,
            additional_headers={"Authorization": f"Bearer {server.token}"},
            subprotocols=["bazaar.protobuf.v2"],
        ) as websocket:
            await websocket.recv()
            command = load_spec_message(
                "02_advertise_water_for_food.textproto",
                placeholder_values={"RUN_ID": server.run_id},
            )
            await websocket.send(command.SerializeToString())
            reply = pb.ServerMessage.FromString(await websocket.recv())

            assert reply.protocol_error.code == pb.CONTROL_CODE_BAD_MESSAGE
            assert reply.protocol_error.close_session is False
            assert websocket.state.name == "OPEN"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    (
        ("protocol_version", "1.0", pb.CONTROL_CODE_UNSUPPORTED_VERSION),
        ("run_id", "different-run", pb.CONTROL_CODE_RUN_MISMATCH),
    ),
)
async def test_protocol_or_run_mismatch_sends_error_then_closes(
    field, value, expected_code
):
    async with FakeServer() as server:
        async with connect(
            server.url,
            additional_headers={"Authorization": f"Bearer {server.token}"},
            subprotocols=["bazaar.protobuf.v2"],
        ) as websocket:
            await websocket.recv()
            command = load_spec_message(
                "01_ready.textproto", placeholder_values={"RUN_ID": server.run_id}
            )
            setattr(command.ready, field, value)
            await websocket.send(command.SerializeToString())
            reply = pb.ServerMessage.FromString(await websocket.recv())

            assert reply.protocol_error.code == expected_code
            assert reply.protocol_error.close_session is True
            with pytest.raises(ConnectionClosed):
                await websocket.recv()


def test_sync_is_allowed_before_readiness_and_only_advances_sequence():
    scenario = Scenario.create(run_id="fake-run-1")
    sync = load_spec_message(
        "10_sync.textproto", placeholder_values={"RUN_ID": scenario.run_id}
    )

    updated, replies = scenario.handle(sync)

    assert updated.ready is False
    assert updated.world_version == 2
    assert updated.snapshot_sequence == 2
    assert replies[0].state.world_version == 2
    assert replies[0].state.snapshot_sequence == 2


def test_exact_retry_returns_stored_result_and_new_state_without_repeating_action():
    scenario = Scenario.create(run_id="fake-run-1")
    ready = load_spec_message(
        "01_ready.textproto", placeholder_values={"RUN_ID": scenario.run_id}
    )
    scenario, _ = scenario.handle(ready)
    advertise = load_spec_message(
        "02_advertise_water_for_food.textproto",
        placeholder_values={"RUN_ID": scenario.run_id},
    )
    scenario, first_replies = scenario.handle(advertise)

    retried, retry_replies = scenario.handle(advertise)

    assert retried.world_version == 3
    assert retried.snapshot_sequence == 3
    assert retried.next_step == 3
    assert retry_replies[0] == first_replies[0]
    assert retry_replies[1].state.world_version == 3


def test_changed_command_with_reused_id_returns_request_id_conflict():
    scenario = Scenario.create(run_id="fake-run-1")
    ready = load_spec_message(
        "01_ready.textproto", placeholder_values={"RUN_ID": scenario.run_id}
    )
    scenario, _ = scenario.handle(ready)
    advertise = load_spec_message(
        "02_advertise_water_for_food.textproto",
        placeholder_values={"RUN_ID": scenario.run_id},
    )
    scenario, _ = scenario.handle(advertise)
    changed = pb.ClientMessage()
    changed.CopyFrom(advertise)
    changed.advertise.body.expires_tick = 5

    updated, replies = scenario.handle(changed)

    assert updated.world_version == 3
    assert updated.next_step == 3
    assert replies[0].result.ok is False
    assert replies[0].result.code == pb.RESULT_CODE_REQUEST_ID_CONFLICT
    assert replies[1].state.world_version == 3


@pytest.mark.parametrize(
    "filename",
    (
        "03_advertise_seeking_components.textproto",
        "04_offer_water_for_food.textproto",
        "07_accept_gift.textproto",
        "08_withdraw_advertisement.textproto",
        "09_advertise_over_request_limit.textproto",
    ),
)
def test_new_out_of_order_command_ends_scenario(filename):
    scenario = Scenario.create(run_id="fake-run-1")
    ready = load_spec_message(
        "01_ready.textproto", placeholder_values={"RUN_ID": scenario.run_id}
    )
    scenario, _ = scenario.handle(ready)
    command = load_spec_message(
        filename,
        placeholder_values={
            "RUN_ID": scenario.run_id,
            "ADVERTISEMENT_ID": "not-yet-created",
            "ZERO_PRICE_OFFER_ID": "not-yet-created",
        },
    )

    with pytest.raises(ValueError, match="scenario mismatch"):
        scenario.handle(command)
