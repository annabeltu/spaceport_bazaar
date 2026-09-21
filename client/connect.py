#!/usr/bin/env python3
"""Connect to the practice server and complete its readiness handshake.

Run inside the dev container, with the server already running in another
terminal (`bash scripts/run_server.sh`):

    python client/connect.py
    python client/connect.py --credentials /some/other/validation-credentials.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from google.protobuf import text_format
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[1]
# The generated protobuf code lives in src/generated in this repo.
sys.path.insert(0, str(ROOT / "src"))

from generated import bazaar_pb2  # noqa: E402


# scripts/run_server.sh starts the server from the repo root, so that's
# where the server writes this file.
DEFAULT_CREDENTIALS = ROOT / "validation-credentials.json"
URI = "ws://127.0.0.1:3001/ws"
SUBPROTOCOL = "bazaar.protobuf.v2"


def p01_token(credentials: Path) -> str:
    try:
        data = json.loads(credentials.read_text())
    except FileNotFoundError as error:
        # Show the path as given: it may be outside the repo (e.g. in /tmp).
        raise SystemExit(f"Missing {credentials}. Start the server first.") from error

    player = next(
        (item for item in data.get("players", []) if item.get("station_id") == "P01"),
        None,
    )
    if not player or not player.get("token"):
        raise SystemExit("The credentials file has no token for station P01.")
    return player["token"]


def decode(payload: bytes) -> bazaar_pb2.ServerMessage:
    if not isinstance(payload, bytes):
        raise RuntimeError("Server sent text; binary Protobuf was expected.")
    message = bazaar_pb2.ServerMessage()
    message.ParseFromString(payload)
    return message


async def main(credentials: Path) -> None:
    async with connect(
        URI,
        additional_headers={"Authorization": f"Bearer {p01_token(credentials)}"},
        subprotocols=[SUBPROTOCOL],
    ) as websocket:
        if websocket.subprotocol != SUBPROTOCOL:
            raise RuntimeError(
                f"Server selected {websocket.subprotocol!r}, expected {SUBPROTOCOL!r}."
            )

        first = decode(await websocket.recv())
        if first.WhichOneof("message") != "state":
            raise RuntimeError(f"Expected initial state, got {first.WhichOneof('message')}")

        state = first.state
        print(
            f"Connected as {state.self_station_id}: run={state.run_id}, "
            f"snapshot={state.snapshot_sequence}, world={state.world_version}",
            flush=True,
        )
        print(text_format.MessageToString(first, as_utf8=True))

        ready = bazaar_pb2.ClientMessage()
        ready.ready.type = bazaar_pb2.READY_TYPE_READY
        ready.ready.protocol_version = "2.0"
        ready.ready.run_id = state.run_id
        ready.ready.ready = True
        ready.ready.snapshot_sequence = state.snapshot_sequence
        await websocket.send(ready.SerializeToString())

        response = decode(await websocket.recv())
        if response.WhichOneof("message") != "readiness" or not response.readiness.ready:
            raise RuntimeError(f"Readiness was not confirmed:\n{response}")
        print(
            "Readiness confirmed. The connection is ready for trading commands.",
            flush=True,
        )

        advertisement = bazaar_pb2.ClientMessage()
        command = advertisement.advertise
        command.type = bazaar_pb2.ADVERTISE_TYPE_ADVERTISE
        command.protocol_version = "2.0"
        command.run_id = state.run_id
        command.request_id = "student-advertise-1"
        command.body.selling.items.append(bazaar_pb2.RESOURCE_WATER)
        command.body.seeking.items.append(bazaar_pb2.RESOURCE_FOOD)
        command.body.expires_tick = 6
        await websocket.send(advertisement.SerializeToString())

        result = decode(await websocket.recv())
        print(text_format.MessageToString(result, as_utf8=True))
        if (
            result.WhichOneof("message") != "result"
            or result.result.request_id != command.request_id
            or not result.result.ok
            or result.result.code != bazaar_pb2.RESULT_CODE_OK
        ):
            raise RuntimeError(f"Advertisement was not confirmed: {result}")

        update = decode(await websocket.recv())
        print(text_format.MessageToString(update, as_utf8=True))
        if update.WhichOneof("message") != "state":
            raise RuntimeError(f"Expected advertisement state: {update}")
        state = update.state
        inventory = getattr(state, "self").inventory
        if (inventory.water, inventory.food, inventory.components) != (30, 30, 30):
            raise RuntimeError("Step 2 inventory should still be (30, 30, 30).")
        if not any(
            item.station_id == state.self_station_id
            and list(item.selling.items) == [bazaar_pb2.RESOURCE_WATER]
            and list(item.seeking.items) == [bazaar_pb2.RESOURCE_FOOD]
            and item.expires_tick == 6
            and item.status == bazaar_pb2.PUBLICATION_STATUS_ACTIVE
            for item in state.advertisements.items
        ):
            raise RuntimeError("The active water-for-food advertisement is missing.")
        if state.world_version != 3 or state.snapshot_sequence != 2:
            raise RuntimeError("Step 2 expected world version 3 and snapshot sequence 2.")
        print("Step 2 confirmed: advertised water for food; inventory unchanged.", flush=True)

        # Keep receiving because state updates are server-pushed. Add subsequent
        # commands before this loop as you implement the remaining README steps.
        async for payload in websocket:
            print(text_format.MessageToString(decode(payload), as_utf8=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--credentials",
        type=Path,
        default=DEFAULT_CREDENTIALS,
        help="the server's validation-credentials.json (default: repo root)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        asyncio.run(main(args.credentials))
    except KeyboardInterrupt:
        pass
