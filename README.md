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

Terminal 2 connects as P01 and completes the required readiness handshake:

```sh
.venv/bin/python client/connect.py
```

The server must remain running because restarting it creates a new run and new
credentials. Both commands must run inside the same devcontainer so the local
WebSocket URL `ws://127.0.0.1:3001/ws` reaches the server.

The connection client prints the decoded initial state and then keeps reading
server-pushed messages. Implement the trading commands described in the starter
guide before its final receive loop.

## Regenerate Python Protobuf bindings

The post-create step generates `generated/bazaar_pb2.py`. If the schema changes,
regenerate it with:

```sh
mkdir -p generated
touch generated/__init__.py
protoc --python_out=generated --proto_path=starter starter/bazaar.proto
```
