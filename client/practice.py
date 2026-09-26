#!/usr/bin/env python3
"""Connect to the local practice server and complete practice steps 1 through 10."""

# Step numbers in this file refer to starter/README.md, "Complete the exchange".

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
from client.messages import build_ready, build_advertisement, build_offer, build_accept, build_withdraw, build_sync  # noqa: E402
from client.state import (  # noqa: E402
    require, validate_initial, validate_progress, validate_advertisement,
    validate_result, validate_offer, validate_acceptance, validate_gift,
    validate_accept_result, validate_gift_accepted, recover_offer_id, validate_withdrawn, validate_request_capacity_error, validate_final,
)


CREDENTIALS = ROOT / "starter" / "validation-credentials.json"
URI = "ws://127.0.0.1:3001/ws"
SUBPROTOCOL = "bazaar.protobuf.v2"


# Step 1 setup: load P01 credentials for the authenticated WebSocket connection.
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


# Reconnect support for steps 9-10: determine whether step 9 already completed.
def capacity_error_observed(run_id):
    """The rejected request is not stored in snapshots; consult this server's report."""
    report_path = ROOT / "starter" / "validation-report.json"
    try:
        report = json.loads(report_path.read_text())
    except FileNotFoundError:
        return False
    require(report.get("run_id") == run_id or not report.get("run_id"),
            "Validation report belongs to a different run; check the server report path.")
    if report.get("run_id") != run_id:
        return False
    require(report.get("status") != "scenario mismatch",
            "This server run ended with a scenario mismatch. Restart the practice server, then rerun the client.")
    return report.get("last_completed_step", 0) >= 9


# Exercise steps 1-10, numbered below to match starter/README.md.
async def main() -> None:
    # Step 1: connect, read the starting state, and confirm readiness.
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
        skip_capacity_request = state.world_version == 9 and capacity_error_observed(run_id)
        await send(websocket, build_ready(run_id, state.snapshot_sequence))
        readiness = (await receive(websocket, "readiness")).readiness
        require(readiness.ready and readiness.run_id == run_id
                and readiness.snapshot_sequence == state.snapshot_sequence,
                "Readiness confirmation does not match the initial state.")
        print("Readiness confirmed.", flush=True)

        async def execute(message, version, inventory=(30, 30, 30)):
            nonlocal state
            command = getattr(message, message.WhichOneof("message"))
            sequence = state.snapshot_sequence + 1
            await send(websocket, message)
            result = (await receive(websocket, "result")).result
            object_id = validate_result(result, run_id, command.request_id)
            update = (await receive(websocket, "state")).state
            require(update.run_id == run_id and update.self_station_id == "P01",
                    "State belongs to another run or station.")
            validate_progress(update, version, sequence, inventory)
            state = update
            return object_id

        if state.world_version < 9:
            # A reconnect supplies current progress; do not replay obsolete checks.
            # Step 2: advertise water for food and check that inventory is unchanged.
            if state.world_version == 2:
                object_id = await execute(build_advertisement(
                    run_id, "student-advertise-1", [pb.RESOURCE_WATER], [pb.RESOURCE_FOOD]), 3)
                validate_advertisement(state, [pb.RESOURCE_WATER], [pb.RESOURCE_FOOD], object_id)
                print("Step 2 confirmed: advertised water for food; inventory unchanged.", flush=True)
            # Step 3: replace the advertisement with a components request; save its ID.
            if state.world_version == 3:
                validate_advertisement(state, [pb.RESOURCE_WATER], [pb.RESOURCE_FOOD])
                object_id = await execute(build_advertisement(
                    run_id, "student-advertise-seeking-1", [], [pb.RESOURCE_COMPONENTS]), 4)
                validate_advertisement(state, [], [pb.RESOURCE_COMPONENTS], object_id)
            advertisement_id = validate_advertisement(state, [], [pb.RESOURCE_COMPONENTS])
            print(f"Step 3 confirmed: seeking components; ADVERTISEMENT_ID={advertisement_id}", flush=True)

            if state.world_version < 8:
                # Step 4: send the trade offer, or recover its ID when reconnecting.
                if state.world_version == 4:
                    offer_id = await execute(build_offer(run_id), 5)
                else:
                    offer_id = recover_offer_id(state)
                if state.world_version == 5:
                    validate_offer(state, offer_id)
                print(f"Step 4 confirmed: offered two water for one food; OFFER_ID={offer_id}", flush=True)

                # P02's commands produce states, not command results for this client.
                for version in range(state.world_version + 1, 8):
                    update = (await receive(websocket, "state")).state
                    require(update.run_id == run_id and update.self_station_id == "P01",
                            "State belongs to another run or station.")
                    validate_progress(update, version, state.snapshot_sequence + 1, (28, 31, 30))
                    validate_acceptance(update, offer_id)
                    state = update
                    # Step 5: the acceptance and inventory checks above confirm the trade.
                    if version == 6:
                        print("Step 5 confirmed: P02 accepted; one transaction; inventory (28, 31, 30).", flush=True)
                    else:
                        # Step 6: verify the gift and record ZERO_PRICE_OFFER_ID.
                        zero_price_offer_id = validate_gift(state)
                        print(f"Step 6 confirmed: P02 offered one component for free; ZERO_PRICE_OFFER_ID={zero_price_offer_id}", flush=True)

                # Step 7: accept the gift and validate the result and updated state.
                zero_price_offer_id = validate_gift(state)
                command = build_accept(run_id, zero_price_offer_id)
                await send(websocket, command)
                result = (await receive(websocket, "result")).result
                transaction_id = validate_accept_result(
                    result, run_id, command.accept.request_id, zero_price_offer_id)
                update = (await receive(websocket, "state")).state
                require(update.run_id == run_id and update.self_station_id == "P01",
                        "State belongs to another run or station.")
                validate_progress(update, 8, state.snapshot_sequence + 1, (28, 31, 31))
                validate_gift_accepted(update, zero_price_offer_id, transaction_id, advertisement_id)
                state = update
                print(f"Step 7 confirmed: gift accepted; two transactions; inventory (28, 31, 31); seeking advertisement active; TRANSACTION_ID={transaction_id}", flush=True)


            # Step 8: withdraw the advertisement and verify that trades remain unchanged.
            previous_transactions = pb.ListTransaction()
            previous_transactions.CopyFrom(state.transactions)
            object_id = await execute(build_withdraw(run_id, advertisement_id), 9, (28, 31, 31))
            require(object_id == advertisement_id, "Withdrawal result identifies a different advertisement.")
            validate_withdrawn(state, advertisement_id, previous_transactions)
            print("Step 8 confirmed: advertisement removed; two transactions; inventory (28, 31, 31).", flush=True)

        # Step 9: exceed request capacity and validate the expected protocol error.
        if not skip_capacity_request:
            # This rejected command yields only a protocol error, with no state update.
            request_id = "student-advertise-2"
            await send(websocket, build_advertisement(
                run_id, request_id, [pb.RESOURCE_WATER], [pb.RESOURCE_FOOD]))
            error = (await receive(websocket, "protocol_error")).protocol_error
            validate_request_capacity_error(error, run_id, request_id)
            print("Step 9 confirmed: request capacity exceeded as expected; connection remains open.", flush=True)


        # Step 10: request and validate the final snapshot on the same connection.
        await send(websocket, build_sync(run_id))
        update = (await receive(websocket, "state")).state
        validate_final(update, run_id, state.snapshot_sequence + 1, state.transactions)
        state = update
        print("Step 10 confirmed: final state decoded and validated; five stored results; two transactions; inventory (28, 31, 31); imports (0, 1, 1); exports (2, 0, 0); no simulation ticks.", flush=True)



# Program entry point: run the complete exercise.
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
