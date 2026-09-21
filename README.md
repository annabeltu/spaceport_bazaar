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

Terminal 2 connects as P01 and completes practice steps 1 through 4:

```sh
.venv/bin/python client/connect.py
```

The server must remain running because restarting it creates a new run and new
credentials. Both commands must run inside the same devcontainer so the local
WebSocket URL `ws://127.0.0.1:3001/ws` reaches the server.

The client validates the initial state, confirms readiness, publishes and replaces
an advertisement, then offers P02 two water for one food. It checks each command's
result and state before continuing, then prints server-pushed updates (including
P02's acceptance and gift). It does not yet accept the gift.

You can reconnect after step 2 or 3 without restarting the server: the client
validates the current listing and continues from the corresponding world version.
Snapshot sequences are checked relative to the connection's latest snapshot.
Reconnects after step 4 are not yet supported.

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
