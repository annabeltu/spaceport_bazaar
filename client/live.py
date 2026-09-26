#!/usr/bin/env python3
"""Connect to a remote server and trade resources for survival."""
import argparse
import asyncio
import getpass
import os
from pathlib import Path
import sys
from websockets.asyncio.client import connect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from client.connection import receive, send
from client.messages import build_ready
from client.trading import Trader, describe

async def watch(url, token, once, ready=False, trade=True, cooperate=False):
    trade = trade and not once
    ready = ready or trade
    trader = Trader(cooperate=cooperate)
    protocol = "bazaar.protobuf.v2"
    async with connect(url, additional_headers={"Authorization": f"Bearer {token}"},
                       subprotocols=[protocol], open_timeout=20) as websocket:
        if websocket.subprotocol != protocol:
            raise RuntimeError("Server did not select bazaar.protobuf.v2.")
        print("Connected and authenticated.", flush=True)
        initial = await asyncio.wait_for(receive(websocket, "state"), timeout=30)
        state = initial.state
        trader.observe(state)
        print(describe(state), flush=True)
        if ready:
            await send(websocket, build_ready(state.run_id, state.snapshot_sequence))
            async with asyncio.timeout(30):
                while True:
                    message = await receive(websocket)
                    kind = message.WhichOneof("message")
                    if kind == "protocol_error":
                        raise RuntimeError("Server rejected the readiness declaration.")
                    if kind == "state":
                        initial = message
                        trader.observe(message.state)
                    if kind == "readiness":
                        confirmation = message.readiness
                        if (not confirmation.ready or confirmation.run_id != state.run_id
                                or confirmation.snapshot_sequence != state.snapshot_sequence):
                            raise RuntimeError("Readiness confirmation did not match the declaration.")
                        print("Readiness confirmed. Listening for updates.", flush=True)
                        break
        async def handle_state(state):
            print(describe(state), flush=True)
            if trade:
                for command in trader.plan(state):
                    print(f"Trading: {command.WhichOneof('message')}", flush=True)
                    await send(websocket, command)

        if not once:
            await handle_state(initial.state)
            while True:
                message = await receive(websocket)
                kind = message.WhichOneof("message")
                if kind == "state":
                    await handle_state(message.state)
                elif kind == "protocol_error":
                    raise RuntimeError(f"Server rejected a command: {message.protocol_error}")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="wss://spaceport.edneo.com/ws")
    parser.add_argument("--once", action="store_true", help="Read initial state, then disconnect")
    parser.add_argument("--ready", action="store_true", help="Declare readiness and wait for confirmation")
    parser.add_argument("--observe", action="store_true", help="Only display state; disable automatic trading")
    parser.add_argument("--cooperate", action="store_true", help="Offer small surplus gifts to peers advertising need")
    args = parser.parse_args()
    if args.ready and args.once:
        parser.error("--ready cannot be combined with --once; stay connected while ready")
    token = os.environ.get("SPACEPORT_CLIENT_TOKEN") or getpass.getpass("Client token: ")
    if not token.strip():
        parser.error("A client token is required")
    try:
        asyncio.run(watch(args.url, token.strip(), args.once, args.ready, trade=not args.observe, cooperate=args.cooperate))
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
