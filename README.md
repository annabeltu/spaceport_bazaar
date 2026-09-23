# Spaceport Bazaar client

This repository contains a Python devcontainer for the practice server in
[`starter/README.md`](starter/README.md).

## Start in VS Code

1. Make sure Docker Desktop is running.
2. Open this repository in VS Code.
3. Run **Dev Containers: Reopen in Container** from the command palette.
4. Wait for the post-create setup to finish.

Then open two terminals inside the container.

Terminal 1 starts the matching ARM64 or x86-64 Linux server binary:

```sh
bash scripts/start-server.sh
```

Terminal 2 connects as P01 and completes practice steps 1 through 10:

```sh
.venv/bin/python client/connect.py
```

The server must remain running because restarting it creates a new run and new
credentials. Both commands must run inside the same devcontainer so the local
WebSocket URL `ws://127.0.0.1:3001/ws` reaches the server.

The client validates the initial state, confirms readiness, publishes and replaces
an advertisement, then offers P02 two water for one food. It checks each command's
result and state before continuing, then validates P02's automatic acceptance
and gift, including inventory and transaction checks. It prints the gift's
`ZERO_PRICE_OFFER_ID`, then accepts the gift and validates the result and state.
After confirming two transactions, inventory `(28,31,31)`, and the still-active
components advertisement, it withdraws the advertisement and checks that inventory and both transactions
remain unchanged. It then sends `student-advertise-2` and checks the intentional
request-capacity protocol error. No result or state is expected for that request.
On the same connection, it sends sync and validates the final state, including
five stored results, trade totals, and zero simulation counters, then exits.
A fresh uninterrupted run sends 8 messages and receives 16; reconnects change
these counts and restart snapshot sequences. The server report in
`starter/validation-report.json` should show `sample exchange completed`,
`last_completed_step: 10`, and final inventory `(28,31,31)`.

You can reconnect through step 8 without restarting the server: the client
validates the current state and recovers existing offer IDs before continuing.
Snapshot sequences are checked relative to the connection's latest snapshot.
At world version 8 the client proceeds directly to withdrawal. At version 9 it
checks the matching local validation report: after step 8 it sends the capacity
request; after step 9 it proceeds directly to sync. The rejected request is not
stored in state, and world version 9 alone cannot distinguish these stages.
A run marked `scenario mismatch` must be restarted.

Message construction lives in `client/messages.py`, binary transport in
`client/connection.py`, and snapshot/result checks in `client/state.py`.
`client/connect.py` coordinates the exercise and retains the latest state and
advertisement/offer IDs.

## Run automated tests

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

The tests cover command serialization, required empty resource containers and
zero quantities, response decoding, initial-state checks, and selected command
validation failures. They run without contacting the practice server.

## Regenerate Python Protobuf bindings

The post-create step generates `generated/bazaar_pb2.py`. If the schema changes,
regenerate it with:

```sh
mkdir -p generated
touch generated/__init__.py
protoc --python_out=generated --proto_path=starter starter/bazaar.proto
```

## Connect to the remote server

```sh
.venv/bin/python client/live.py
```

Paste your client token at the hidden prompt (or set `SPACEPORT_CLIENT_TOKEN`).
This connects to `wss://spaceport.edneo.com/ws` and displays incoming state.
By default it only listens. To declare readiness and keep listening, run:

```sh
.venv/bin/python client/live.py --ready
```

Wait for `Readiness confirmed`. The instructor controls when the run starts.
The client does not send trading commands. Press Ctrl+C to disconnect.
Use `--once` to verify authentication and read one state, or `--url` for another server.
The observer dashboard is at <https://spaceport.edneo.com/>; use your separate
observer token there.
