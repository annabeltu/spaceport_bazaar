"""Public API and terminal-failure tests for the fake server."""

import json

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from generated import bazaar_pb2 as pb

from fake_server.server import FakeServer, Mode
from spec import load_spec_message


def test_write_credentials_uses_real_shape_and_obviously_fake_tokens(tmp_path):
    path = tmp_path / "credentials.json"
    server = FakeServer()

    server.write_credentials(path)

    credentials = json.loads(path.read_text())
    assert len(credentials["instructor_token"]) == 64
    assert credentials["players"] == [
        {"token": server.token, "run_id": server.run_id, "station_id": "P01"},
        {"token": server.p02_token, "run_id": server.run_id, "station_id": "P02"},
    ]
    assert all(len(player["token"]) == 64 for player in credentials["players"])


@pytest.mark.asyncio
async def test_out_of_order_socket_command_closes_with_scenario_mismatch():
    async with FakeServer() as server:
        async with connect(
            server.url,
            additional_headers={"Authorization": f"Bearer {server.token}"},
            subprotocols=["bazaar.protobuf.v2"],
        ) as websocket:
            await websocket.recv()
            ready = load_spec_message(
                "01_ready.textproto", placeholder_values={"RUN_ID": server.run_id}
            )
            await websocket.send(ready.SerializeToString())
            await websocket.recv()
            out_of_order = load_spec_message(
                "04_offer_water_for_food.textproto",
                placeholder_values={"RUN_ID": server.run_id},
            )
            await websocket.send(out_of_order.SerializeToString())

            with pytest.raises(ConnectionClosed) as caught:
                await websocket.recv()

    assert caught.value.rcvd.reason == "scenario mismatch"
    assert server.received[-1].WhichOneof("message") == "offer"


@pytest.mark.asyncio
async def test_reconnect_keeps_progress_resets_sequence_and_requires_readiness():
    async with FakeServer() as server:
        options = {
            "additional_headers": {"Authorization": f"Bearer {server.token}"},
            "subprotocols": ["bazaar.protobuf.v2"],
        }
        async with connect(server.url, **options) as websocket:
            await websocket.recv()
            ready = load_spec_message(
                "01_ready.textproto", placeholder_values={"RUN_ID": server.run_id}
            )
            await websocket.send(ready.SerializeToString())
            await websocket.recv()
            advertise = load_spec_message(
                "02_advertise_water_for_food.textproto",
                placeholder_values={"RUN_ID": server.run_id},
            )
            await websocket.send(advertise.SerializeToString())
            await websocket.recv()
            await websocket.recv()

        async with connect(server.url, **options) as websocket:
            current = pb.ServerMessage.FromString(await websocket.recv())
            assert current.state.world_version == 3
            assert current.state.snapshot_sequence == 1
            step_3 = load_spec_message(
                "03_advertise_seeking_components.textproto",
                placeholder_values={"RUN_ID": server.run_id},
            )
            await websocket.send(step_3.SerializeToString())
            rejection = pb.ServerMessage.FromString(await websocket.recv())
            assert rejection.protocol_error.code == pb.CONTROL_CODE_BAD_MESSAGE


@pytest.mark.asyncio
async def test_wrong_subprotocol_mode_accepts_but_confirms_none():
    async with FakeServer(mode=Mode.WRONG_SUBPROTOCOL) as server:
        async with connect(
            server.url,
            additional_headers={"Authorization": f"Bearer {server.token}"},
            subprotocols=["bazaar.protobuf.v2"],
        ) as websocket:
            assert websocket.subprotocol is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_type"),
    ((Mode.GARBAGE, bytes), (Mode.TEXT, str)),
)
async def test_garbage_modes_send_bad_frame_after_initial_state(mode, expected_type):
    async with FakeServer(mode=mode) as server:
        async with connect(
            server.url,
            additional_headers={"Authorization": f"Bearer {server.token}"},
            subprotocols=["bazaar.protobuf.v2"],
        ) as websocket:
            first = pb.ServerMessage.FromString(await websocket.recv())
            bad_frame = await websocket.recv()

            assert first.HasField("state")
            assert isinstance(bad_frame, expected_type)
            if isinstance(bad_frame, bytes):
                parsed = pb.ServerMessage()
                with pytest.raises(Exception):
                    parsed.ParseFromString(bad_frame)


@pytest.mark.asyncio
async def test_close_session_mode_sends_error_then_closes_at_chosen_command():
    async with FakeServer(mode=Mode.CLOSE_SESSION, trigger_after_messages=1) as server:
        async with connect(
            server.url,
            additional_headers={"Authorization": f"Bearer {server.token}"},
            subprotocols=["bazaar.protobuf.v2"],
        ) as websocket:
            await websocket.recv()
            ready = load_spec_message(
                "01_ready.textproto", placeholder_values={"RUN_ID": server.run_id}
            )
            await websocket.send(ready.SerializeToString())
            reply = pb.ServerMessage.FromString(await websocket.recv())

            assert reply.protocol_error.close_session is True
            with pytest.raises(ConnectionClosed):
                await websocket.recv()


@pytest.mark.asyncio
async def test_new_run_id_mode_changes_the_state_run_id():
    async with FakeServer(mode=Mode.NEW_RUN_ID) as server:
        async with connect(
            server.url,
            additional_headers={"Authorization": f"Bearer {server.token}"},
            subprotocols=["bazaar.protobuf.v2"],
        ) as websocket:
            message = pb.ServerMessage.FromString(await websocket.recv())
            assert message.state.run_id != server.run_id


@pytest.mark.asyncio
async def test_drop_mode_closes_after_chosen_reply_then_accepts_reconnect():
    async with FakeServer(mode=Mode.DROP, trigger_after_messages=1) as server:
        options = {
            "additional_headers": {"Authorization": f"Bearer {server.token}"},
            "subprotocols": ["bazaar.protobuf.v2"],
        }
        async with connect(server.url, **options) as websocket:
            await websocket.recv()
            ready = load_spec_message(
                "01_ready.textproto", placeholder_values={"RUN_ID": server.run_id}
            )
            await websocket.send(ready.SerializeToString())
            await websocket.recv()
            with pytest.raises(ConnectionClosed):
                await websocket.recv()

        async with connect(server.url, **options) as websocket:
            state = pb.ServerMessage.FromString(await websocket.recv())
            assert state.state.snapshot_sequence == 1
            assert state.state.world_version == 2
