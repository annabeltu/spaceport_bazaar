#!/usr/bin/env bash
# Live check (Phase 5): run our client against a FRESH practice server, then
# ask scripts/check_report.py whether the server's report shows a passing run.
#
# Run it INSIDE the dev container. The server is a Linux program, and it
# only accepts connections from the same machine.
#
#   bash scripts/run_live_check.sh
#
# What it does:
#   1. Makes a new, private temp folder for this run.
#   2. Starts the practice server in the background, writing its
#      credentials file and report into that folder.
#   3. Waits until the server is listening.
#   4. Runs the client:
#        PYTHONPATH=src python -m bazaar_client --credentials <that file>
#   5. Stops the server and runs check_report.py on its report.
#
# Whatever happens, even a failed step or Ctrl+C, it stops the server and
# deletes the temp folder (see `cleanup` below). It exits 0 only if the
# client AND the report both pass.
#
# To run a different client command, pass it as arguments. They replace
# `python -m bazaar_client`, and `--credentials <file>` is always added at
# the end. The tests use this to run fake clients. For example:
#
#   bash scripts/run_live_check.sh python -m bazaar_client --log-file run.log
#   bash scripts/run_live_check.sh false     # a "client" that always fails
#
# Why a temp folder: every run gets fresh credentials (new tokens) that never
# touch the repo folder, so they can't be committed by accident. mktemp
# makes the folder readable only by you, and it's deleted at the end.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

STARTER_DIR="bazaar-protobuf-starter-linux"
# The client's default URL is ws://127.0.0.1:3001/ws, so if you change the
# port here, pass the client a matching --url too.
SERVER_HOST="127.0.0.1"
SERVER_PORT="3001"
SERVER_START_TIMEOUT_SECONDS=15
SERVER_STOP_TIMEOUT_SECONDS=5
# The practice exchange takes seconds. This only stops a stuck client from
# hanging the script forever.
CLIENT_TIMEOUT_SECONDS=120
TIMEOUT_EXIT_CODE=124  # what `timeout` exits with when time runs out

if [ "$#" -gt 0 ]; then
  CLIENT_COMMAND=("$@")
else
  CLIENT_COMMAND=(python -m bazaar_client)
fi

# --- Checks before starting anything ------------------------------------------

if [ "$(uname -s)" != "Linux" ]; then
  echo "run_live_check: run this inside the dev container. The practice server is a Linux program." >&2
  exit 1
fi
if ! command -v ss >/dev/null 2>&1; then
  echo "run_live_check: needs the 'ss' command (package iproute2), which the dev container has." >&2
  exit 1
fi

# Same choice as scripts/run_server.sh: `uname -m` reports the CPU type, and
# each CPU type has its own server program.
CPU="$(uname -m)"
case "$CPU" in
  x86_64 | amd64)   SERVER_BINARY="spaceport-validate-linux-x86_64" ;;
  aarch64 | arm64)  SERVER_BINARY="spaceport-validate-linux-arm64" ;;
  *)
    echo "run_live_check: unsupported CPU type: $CPU" >&2
    exit 1
    ;;
esac

# Check the server's fingerprint right before running it.
echo "Checking the server's fingerprint..."
bash scripts/verify_checksums.sh starter

# port_listener: prints what is listening on our port, or nothing.
# `ss` reads the system's table of open sockets. Unlike a test connection,
# this never touches the server itself, so it can't disturb the exercise.
port_listener() {
  ss -Hltnp "sport = :$SERVER_PORT"
}

# If an old server were still running, our new server couldn't start, and
# the client would connect to the OLD one using the NEW one's token.
if [ -n "$(port_listener)" ]; then
  echo "run_live_check: something is already listening on port $SERVER_PORT." >&2
  echo "Stop the other practice server first. Is scripts/run_server.sh running in another terminal?" >&2
  exit 1
fi

# --- Cleanup that always runs -------------------------------------------------

WORK_DIR=""
SERVER_PID=""

stop_server() {
  # Nothing to do if the server never started or was already stopped.
  if [ -z "$SERVER_PID" ]; then
    return 0
  fi

  # SIGINT is what Ctrl+C sends, the stop the README describes. The server
  # shuts down cleanly on it (checked 2026-09-21: exit code 0).
  kill -INT "$SERVER_PID" 2>/dev/null || true
  local waited_tenths=0
  while kill -0 "$SERVER_PID" 2>/dev/null &&
    [ "$waited_tenths" -lt $((SERVER_STOP_TIMEOUT_SECONDS * 10)) ]; do
    sleep 0.1
    waited_tenths=$((waited_tenths + 1))
  done
  # If it ignored that, force it. SIGKILL can't be ignored.
  if kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "run_live_check: the server didn't stop within ${SERVER_STOP_TIMEOUT_SECONDS}s; forcing it." >&2
    kill -KILL "$SERVER_PID" 2>/dev/null || true
  fi
  # `wait` collects the stopped process, so no leftover entry stays in the
  # process table.
  wait "$SERVER_PID" 2>/dev/null || true
  echo "Stopped the practice server (PID $SERVER_PID)."
  SERVER_PID=""
}

# `trap cleanup EXIT` runs cleanup whenever this script ends: success, a
# failed step (set -e), or a signal. That's how the server always stops.
cleanup() {
  local exit_code=$?
  # Every cleanup step must run, even if one of them fails.
  set +e
  stop_server
  if [ -n "$WORK_DIR" ]; then
    rm -rf "$WORK_DIR"   # this deletes the credentials file, and its tokens
  fi
  exit "$exit_code"
}
trap cleanup EXIT
# Turn Ctrl+C (INT) and `kill` (TERM) into a normal exit, so the EXIT trap
# above runs. The server needs this: bash starts background programs so
# they IGNORE Ctrl+C from the terminal, so only cleanup can stop it.
trap 'exit 130' INT
trap 'exit 143' TERM

# --- 1. A fresh temp folder -------------------------------------------------

WORK_DIR="$(mktemp -d)"
CREDENTIALS_FILE="$WORK_DIR/validation-credentials.json"
REPORT_FILE="$WORK_DIR/validation-report.json"
SERVER_LOG="$WORK_DIR/server.log"
echo "Temp folder for this run: $WORK_DIR"

# --- 2. Start the server ----------------------------------------------------
# `&` runs it in the background so this script can carry on. Its output goes
# to a log file instead of mixing with the client's. (Checked 2026-09-21: the
# server prints the credentials file's path, never a token.)

"./$STARTER_DIR/$SERVER_BINARY" --codec protobuf \
  --addr "$SERVER_HOST:$SERVER_PORT" \
  --credential-file "$CREDENTIALS_FILE" \
  --report "$REPORT_FILE" \
  >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!
echo "Started the practice server (PID $SERVER_PID) on $SERVER_HOST:$SERVER_PORT."

# --- 3. Wait until it's listening ---------------------------------------------

wait_for_server() {
  local waited_tenths=0 listener
  while [ "$waited_tenths" -lt $((SERVER_START_TIMEOUT_SECONDS * 10)) ]; do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
      echo "run_live_check: the server stopped during startup. Its output:" >&2
      sed 's/^/  /' "$SERVER_LOG" >&2
      return 1
    fi
    # Ready means OUR server (matched by its PID) is listening, and it has
    # written the credentials file the client needs.
    listener="$(port_listener)"
    case "$listener" in
      *"pid=$SERVER_PID,"*)
        if [ -s "$CREDENTIALS_FILE" ]; then
          return 0
        fi
        ;;
    esac
    sleep 0.1
    waited_tenths=$((waited_tenths + 1))
  done
  echo "run_live_check: the server wasn't ready within ${SERVER_START_TIMEOUT_SECONDS}s." >&2
  return 1
}

if ! wait_for_server; then
  exit 1
fi
echo "The server is listening."

# --- 4. Run the client --------------------------------------------------------
# `|| client_exit=$?` records a failure instead of stopping the script, so
# we still check the report: it shows how far the client got.
# `--foreground` keeps the client able to receive Ctrl+C.

echo
echo "== Running the client: ${CLIENT_COMMAND[*]} --credentials $CREDENTIALS_FILE"
client_exit=0
PYTHONPATH=src timeout --foreground "$CLIENT_TIMEOUT_SECONDS" \
  "${CLIENT_COMMAND[@]}" --credentials "$CREDENTIALS_FILE" || client_exit=$?

# --- 5. Stop the server, then check its report ------------------------------------
# Stopping first means the report can't change while we read it.

echo
stop_server
echo
echo "== Checking the server's report"
report_exit=0
python scripts/check_report.py "$REPORT_FILE" || report_exit=$?

# --- Summary ------------------------------------------------------------------

echo
echo "== Live check summary"
if [ "$client_exit" -eq 0 ]; then
  echo "client: passed"
elif [ "$client_exit" -eq "$TIMEOUT_EXIT_CODE" ]; then
  echo "client: FAILED (still running after ${CLIENT_TIMEOUT_SECONDS}s, so it was stopped)"
else
  echo "client: FAILED (exit $client_exit)"
fi
if [ "$report_exit" -eq 0 ]; then
  echo "report: passed"
else
  echo "report: FAILED (see above)"
fi

if [ "$client_exit" -ne 0 ] || [ "$report_exit" -ne 0 ]; then
  exit 1
fi
echo "LIVE CHECK PASSED"
