"""Public API and terminal-failure tests for the fake server."""

import json

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from generated import bazaar_pb2 as pb

from fake_server.server import FakeServer
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

    assert caught.value.reason == "scenario mismatch"
    assert server.received[-1].WhichOneof("message") == "offer"
