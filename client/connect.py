#!/usr/bin/env python3
"""Connect to the practice server and complete practice steps 1 through 4."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from generated import bazaar_pb2 as pb  # noqa: E402
from client.connection import receive, send  # noqa: E402
from client.messages import build_ready, build_advertisement, build_offer  # noqa: E402
from client.state import (  # noqa: E402
    require, validate_initial, validate_progress, validate_advertisement,
    validate_result, validate_offer,
)


CREDENTIALS = ROOT / "starter" / "validation-credentials.json"
URI = "ws://127.0.0.1:3001/ws"
SUBPROTOCOL = "bazaar.protobuf.v2"


def p01_token() -> str:
    try:
        data = json.loads(CREDENTIALS.read_text())
    except FileNotFoundError as error:
        raise SystemExit(
            f"Missing {CREDENTIALS.relative_to(ROOT)}. Start the server first."
        ) from error

    player = next(
        (item for item in data.get("players", []) if item.get("station_id") == "P01"),
        None,
    )
    if not player or not player.get("token"):
        raise SystemExit("The credentials file has no token for station P01.")
    return player["token"]


async def main() -> None:
    async with connect(
        URI,
        additional_headers={"Authorization": f"Bearer {p01_token()}"},
        subprotocols=[SUBPROTOCOL],
    ) as websocket:
        if websocket.subprotocol != SUBPROTOCOL:
            raise RuntimeError(
                f"Server selected {websocket.subprotocol!r}, expected {SUBPROTOCOL!r}."
            )

        state = (await receive(websocket, "state")).state
        validate_initial(state)
        run_id = state.run_id
        await send(websocket, build_ready(run_id, state.snapshot_sequence))
        readiness = (await receive(websocket, "readiness")).readiness
        require(readiness.ready and readiness.run_id == run_id
                and readiness.snapshot_sequence == state.snapshot_sequence,
                "Readiness confirmation does not match the initial state.")
        print("Readiness confirmed.", flush=True)

        async def execute(message, version):
            nonlocal state
            command = getattr(message, message.WhichOneof("message"))
            sequence = state.snapshot_sequence + 1
            await send(websocket, message)
            result = (await receive(websocket, "result")).result
            object_id = validate_result(result, run_id, command.request_id)
            update = (await receive(websocket, "state")).state
            require(update.run_id == run_id and update.self_station_id == "P01",
                    "State belongs to another run or station.")
            validate_progress(update, version, sequence)
            state = update
            return object_id

        # A reconnect supplies current progress; do not replay obsolete checks.
        if state.world_version == 2:
            object_id = await execute(build_advertisement(
                run_id, "student-advertise-1", [pb.RESOURCE_WATER], [pb.RESOURCE_FOOD]), 3)
            validate_advertisement(state, [pb.RESOURCE_WATER], [pb.RESOURCE_FOOD], object_id)
            print("Step 2 confirmed: advertised water for food; inventory unchanged.", flush=True)
        if state.world_version == 3:
            validate_advertisement(state, [pb.RESOURCE_WATER], [pb.RESOURCE_FOOD])
            object_id = await execute(build_advertisement(
                run_id, "student-advertise-seeking-1", [], [pb.RESOURCE_COMPONENTS]), 4)
            validate_advertisement(state, [], [pb.RESOURCE_COMPONENTS], object_id)
        advertisement_id = validate_advertisement(state, [], [pb.RESOURCE_COMPONENTS])
        print(f"Step 3 confirmed: seeking components; ADVERTISEMENT_ID={advertisement_id}", flush=True)

        offer_id = await execute(build_offer(run_id), 5)
        validate_offer(state, offer_id)
        print(f"Step 4 confirmed: offered two water for one food; OFFER_ID={offer_id}", flush=True)

        # P02's acceptance and gift arrive automatically (steps 5 and 6).
        while True:
            message = await receive(websocket)
            if message.WhichOneof("message") == "state":
                state = message.state


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
