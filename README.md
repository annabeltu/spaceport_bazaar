# Spaceport Bazaar client

A Python client for trading water, food, and components in Spaceport Bazaar. The main workflow uses **your own client token and one connection to your station**. It learns likely resource producers from public advertisements and proposes trades to help stations stay supplied.

The repository also includes a scripted local practice exercise, automated tests, and an optional coordinator that requires nine separate authorized accounts. You do not need the coordinator for single-station play.

## 1. Set up the workspace

1. Install Docker Desktop, VS Code, and the VS Code Dev Containers extension.
2. Start Docker Desktop and open this repository in VS Code.
3. Open the command palette and select **Dev Containers: Reopen in Container**.
4. Wait for the container's setup to finish.

**Run all commands below from the repository root in a terminal inside the container.** The commands use `.venv/bin/python` explicitly, so activating the virtual environment is unnecessary.

The container supplies Python 3.12, the Protobuf compiler, and the Linux libraries needed by the practice server. It automatically runs:

```sh
bash .devcontainer/setup.sh
```

Purpose: create `.venv`, install runtime dependencies, and generate Python message classes from `starter/bazaar.proto`. Run this manually if you need to repeat setup. It does not start a server or install pytest.

Install the development dependencies before running tests:

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
```

Purpose: install pytest and the runtime dependencies listed in `requirements.txt`.

## 2. Run your station on the live server

For automatic trading with optional help for other planets:

```sh
.venv/bin/python client/live.py --cooperate
```

Purpose: connect to `wss://spaceport.edneo.com/ws`, authenticate as your station, declare readiness, and trade while the run is running. Cooperation also allows small surplus gifts to planets advertising a need. Enter your **client token** at the hidden prompt. The instructor controls when the run starts; look for `Readiness confirmed`.

Choose one of these commands for other modes; they are alternatives, not steps to run together:

| Command | Purpose |
| --- | --- |
| `.venv/bin/python client/live.py` | Automatically trade for your station without proposing cooperative gifts. |
| `.venv/bin/python client/live.py --observe` | Display server messages and state without declaring readiness or trading. |
| `.venv/bin/python client/live.py --observe --ready` | Declare readiness and keep listening, with automatic trading disabled. |
| `.venv/bin/python client/live.py --once` | Authenticate, read the initial state, and disconnect without trading. |
| `.venv/bin/python client/live.py --help` | Show the available command-line options. |

Do not combine `--once` with `--ready`. To use a different server, supply its WebSocket address:

```sh
.venv/bin/python client/live.py --url wss://YOUR-SERVER/ws --cooperate
```

Purpose: run the same strategy against the server you specify. Replace `YOUR-SERVER` with the real host.

The client also accepts the `SPACEPORT_CLIENT_TOKEN` environment variable. In Bash, you can set it without putting the token into a command in shell history:

```sh
read -r -s -p 'Client token: ' SPACEPORT_CLIENT_TOKEN
printf '\n'
export SPACEPORT_CLIENT_TOKEN
.venv/bin/python client/live.py --cooperate
unset SPACEPORT_CLIENT_TOKEN
```

Purpose: reuse an environment-provided token for the launch, then remove it from the shell after the client exits. The built-in hidden prompt is sufficient for normal use.

Press **Ctrl+C** to stop a client. Rerun its command to reconnect; it does not automatically reconnect. Connecting again as the same station replaces its previous session. A planet that has permanently failed needs a new run to recover.

The observer dashboard is at <https://spaceport.edneo.com/> and uses your separate **observer token**.

## 3. Understand the trading strategy

The client chooses trades; the server validates commands and settles an offer when its recipient accepts it. An advertisement is a public statement of interest, not a completed trade.

| Message | Meaning |
| --- | --- |
| Advertisement | “I sell water and seek food.” No inventory changes. |
| Offer | “P02, I offer three water for three food.” The recipient must accept. |
| Accept | Agree to a specific offer so the server can attempt settlement. |
| Withdraw | Cancel an advertisement or an open outgoing offer. |
| Ready | Confirm readiness using the received snapshot sequence. |
| Sync | Request a fresh authoritative state snapshot. |

`Trader` in `client/trading.py` follows this strategy:

1. Read your actual specialty, inventory, and upkeep from the server snapshot.
2. Remember each peer's earliest observed advertisement, including observations before readiness is confirmed. If it sells exactly one resource, infer that resource as the peer's primary resource. Empty or mixed first listings remain unknown; later listings do not replace the inference.
3. Seek imported resources for the remaining run, prioritizing those with the fewest ticks of supply left. Preserve two ticks of your own upkeep and account for resources already promised in open offers.
4. Accept useful, affordable incoming trades and advertise your own surplus specialty and resource needs.
5. Propose small equal-quantity trades, up to three units per offer. Prefer current mutually compatible advertisements, then inferred producers requesting your resource, current sellers, and other inferred producers. Unknown partners can receive small exploratory offers when better matches are unavailable.
6. Respect the server's command, request-record, message-size, expiration, and open-offer limits. Plan at most one batch per tick.

With `--cooperate`, the trader may also offer one unit of its specialty for free to a peer actively requesting it. Gifts require a buffer of your own supplies and spare specialty inventory after budgeting offers. The recipient must still accept.

For example, a water producer can remember a peer's first food advertisement and later offer water for food, prioritizing that peer when it requests water.

This uses public information through your one connection. Advertisements do not prove a peer's specialty, inventory, or health. The goal is longer survival across planets, but the client cannot guarantee it or accept trades for anyone else. Advertisement memory is held in the current process and cleared for a new run. Restarting rebuilds it from available advertisements, so joining late can miss the original listing.

## 4. Run the local practice exercise

The local exercise checks protocol behavior using scripted stations P01 and P02. It is separate from the live survival simulation and does not require your live token.

In **terminal 1**, start the practice server:

```sh
bash scripts/start-server.sh
```

Purpose: select the ARM64 or x86-64 Linux binary for the container and start the Protobuf server at `ws://127.0.0.1:3001/ws`. The server creates local validation credentials and a report under `starter/`.

Leave terminal 1 running. In **terminal 2**, run:

```sh
.venv/bin/python client/practice.py
```

Purpose: authenticate as P01 using `starter/validation-credentials.json` and complete the ten steps described in [the practice instructions](starter/README.md). The client:

1. Reads the initial state and confirms readiness.
2. Advertises water for food, then replaces that listing with a components request.
3. Offers P02 two water for one food and checks P02's automatic acceptance.
4. Accepts P02's free component and verifies both transactions.
5. Withdraws its advertisement.
6. Intentionally exceeds request capacity and checks the expected error.
7. Requests the final state and validates inventory, stored results, and trade totals.

Inspect the practice server's completion report:

```sh
.venv/bin/python -m json.tool starter/validation-report.json
```

Purpose: pretty-print the report. Successful completion reports `sample exchange completed`, `last_completed_step: 10`, and final inventory of 28 water, 31 food, and 31 components. The exercise has no simulation ticks. The intentional request-capacity error is part of a successful exercise.

Keep the server running when reconnecting: restarting it creates a new run and credentials. Rerunning `practice.py` can resume supported progress without replaying completed commands. At world version 9 it consults the matching report to determine whether the intentional capacity error has already occurred. If the report says `scenario mismatch`, stop the practice server with Ctrl+C, start it again, and rerun the practice client.

## 5. What each client file does

| File | Responsibility and main functions |
| --- | --- |
| [`client/live.py`](client/live.py) | Entry point for your single live station. `main()` parses options and reads the token. `watch()` connects, confirms readiness, observes snapshots, asks `Trader.plan()` for commands, and sends them. |
| [`client/trading.py`](client/trading.py) | Single-station decision logic. `Trader.observe()` remembers advertisements and infers specialties. `Trader.plan()` selects acceptances, advertisements, trades, and optional gifts. `describe()` formats station status; `quantities()` reads resource bundles. It constructs commands without opening a connection. |
| [`client/messages.py`](client/messages.py) | Builds outgoing Protobuf command objects. `_build()` fills common protocol fields. Helpers build readiness, advertisements, offers, acceptance, withdrawal, and sync messages. `build_offer()` is specifically the practice two-water-for-one-food offer to P02; the live trader builds its own variable offers using `_build()`. |
| [`client/connection.py`](client/connection.py) | Shared transport helpers. `decode()` parses and validates binary server messages; `receive()` reads, prints, and optionally checks a message type; `send()` serializes and transmits a command over an existing WebSocket. |
| [`client/practice.py`](client/practice.py) | Entry point for the local ten-step exercise, formerly named `connect.py`. Loads P01's local credentials, runs the scripted exchange, tracks its latest snapshot and object IDs, and resumes supported practice progress. |
| [`client/state.py`](client/state.py) | Practice-specific validation. `require()` raises an error on an unexpected condition; `bundle()` reads resource quantities. The `validate_*()` functions check snapshots, trades, gifts, withdrawals, errors, and final totals. `recover_offer_id()` finds the existing practice offer after reconnecting. This file is not the live client's state store and is not used by `live.py`. |
| [`client/hive.py`](client/hive.py) | Optional, separate program for controlling nine authorized stations. `load_tokens()` loads credentials; `Session` tracks each connection and waits for results and snapshots; `choose_transfer()` identifies resource transfers; `coordinate()` arranges gifts and acceptance across accounts. It is not launched by `live.py` and is not used with your single token. |
| `client/__pycache__/` | Python-generated compiled cache files, if present. These are not source files and do not need manual editing. |

The live flow is:

```text
Server snapshot → connection.py → live.py → trading.py
                                               ↓
Server ← connection.py ← live.py ← commands built with messages.py
```

`live.py` also builds its readiness message directly through `messages.py`. The practice flow uses `practice.py` to coordinate transport and messages, with `state.py` checking the results. All clients use the generated Protobuf definitions in `generated/bazaar_pb2.py`.

## 6. Run and understand the tests

After installing development dependencies, run all tests:

```sh
.venv/bin/python -m pytest -q
```

Purpose: check protocol helpers, practice behavior, live trading decisions, and coordinator logic. These tests run locally without a running practice server or live credentials. They use constructed snapshots, simulated exchanges, and mocked connections.

Run an individual test file when working on a specific area:

| Command | What the test file checks |
| --- | --- |
| `.venv/bin/python -m pytest -q tests/test_client.py` | Command serialization, required empty fields and zero quantities, binary decoding, practice snapshot validation, trade and gift settlement, withdrawals, capacity errors, final totals, and resuming the practice exercise without replaying commands. |
| `.venv/bin/python -m pytest -q tests/test_trading.py` | Specialty-based decisions, useful versus harmful incoming offers, upkeep reserves, command budgets, pending offers, advertisement inference and partner selection, cooperation limits, and using the latest snapshot after live readiness. |
| `.venv/bin/python -m pytest -q tests/test_hive.py` | Nine-account credential validation, donor reserves, urgent-recipient selection, command budgets, waiting for authoritative settlement state, and a simplified 120-tick resource-sharing simulation. This simulation is not proof of survival on the live server. |

For more detail on each test result:

```sh
.venv/bin/python -m pytest -v
```

To run only advertisement-inference tests while changing that strategy:

```sh
.venv/bin/python -m pytest -q tests/test_trading.py -k 'first_ad or earliest_ad or ambiguous'
```

Purpose: filter tests by name. Test files verify behavior; they are not programs for joining the game.

## 7. Supporting files and Protobuf generation

| File or directory | Purpose |
| --- | --- |
| `requirements.txt` | Runtime dependencies: `protobuf` for message encoding and `websockets` for connections. |
| `requirements-dev.txt` | Includes runtime dependencies and adds pytest. |
| `.devcontainer/devcontainer.json` | VS Code container configuration, automatic setup, Python interpreter, extensions, and port forwarding. |
| `.devcontainer/Dockerfile` | Python 3.12 Linux image and system dependencies, including `protoc`. |
| `.devcontainer/setup.sh` | Creates the virtual environment, installs runtime packages, and generates Protobuf bindings. |
| `scripts/start-server.sh` | Selects and starts the local practice server binary for the container architecture. |
| `starter/README.md` | Detailed practice exercise and protocol instructions. |
| `starter/bazaar.proto` | Schema defining messages, state, resource bundles, commands, and enums. |
| `generated/bazaar_pb2.py` | Generated Python classes used by clients and tests. Regenerate from the schema instead of editing manually. |
| `generated/__init__.py` | Marks the generated directory as a Python package. |
| `starter/validation-credentials.json` | Local practice credentials created by the practice server. |
| `starter/validation-report.json` | Practice server's progress and completion report. |

If the Protobuf schema changes, regenerate the Python classes inside the container:

```sh
mkdir -p generated
touch generated/__init__.py
protoc --python_out=generated --proto_path=starter starter/bazaar.proto
```

Purpose: ensure the generated package exists and compile `bazaar.proto` into `generated/bazaar_pb2.py`. Then run the test suite.

## 8. Optional nine-station coordinator

**Skip this for the normal single-token workflow.** `hive.py` requires nine distinct authorized client tokens and opens a connection for each station. It cannot operate with only your token. The scripted local practice server does not support this scenario.

For someone who already has control of all nine accounts, create a private `hive-tokens.json` mapping each station ID (`P01` through `P09`) to its own token. Stop any individual clients for those stations, then run:

```sh
.venv/bin/python client/hive.py --tokens-file hive-tokens.json
```

Purpose: coordinate resource sharing using all nine stations' private snapshots. The coordinator cancels existing outgoing offers, prioritizes the shortest supply runway, uses lower health to break ties, and transfers actual surplus through gifts that it accepts on the recipient's connection. It targets up to 30 ticks of imported supplies and retains two ticks of a producer's specialty upkeep. It refreshes snapshots after settlement and respects command limits.

The token file is ignored by Git. The coordinator stops on connection failures; rerunning reconstructs state from the server. Its allocation is a heuristic, not a survival guarantee.

Show coordinator options with:

```sh
.venv/bin/python client/hive.py --help
```
