#!/usr/bin/env bash
# Tests for scripts/run_live_check.sh.
#
# Run INSIDE the dev container (it starts the real practice server, which
# is a Linux program):
#
#   bash tests/scripts/test_run_live_check.sh
#
# Our real client doesn't exist yet, so every case passes a small FAKE
# client command to the script instead. The main thing checked after every
# case: the script stopped the server and deleted its temp folder, whether
# the "client" passed, failed, or the script was interrupted.
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LIVE_CHECK="$REPO_ROOT/scripts/run_live_check.sh"
SERVER_PROCESS_PATTERN="spaceport-validate-linux"
PORT="3001"
WORK="$(mktemp -d)"

if [ "$(uname -s)" != "Linux" ]; then
  echo "SETUP FAILED: run this inside the dev container (the server is a Linux program)." >&2
  exit 2
fi
if [ ! -d "$REPO_ROOT/bazaar-protobuf-starter-linux" ]; then
  echo "SETUP FAILED: bazaar-protobuf-starter-linux/ not found. Run: bash scripts/setup.sh" >&2
  exit 2
fi
# These tests check that NO server is left running afterwards, so they
# can't start while one is already running.
if pgrep -f "$SERVER_PROCESS_PATTERN" >/dev/null; then
  echo "SETUP FAILED: a practice server is already running. Stop it first." >&2
  exit 2
fi

# If a test itself goes wrong, don't leave anything behind. (We checked
# above that no server was running before, so any server now is ours.)
cleanup() {
  pkill -f "$SERVER_PROCESS_PATTERN" 2>/dev/null
  rm -rf "$WORK"
}
trap cleanup EXIT

passed=0
failed=0
output=""
exit_code=0

# live_check <client command...>
# Runs the live check with this fake client and saves its output and exit code.
live_check() {
  output="$(bash "$LIVE_CHECK" "$@" 2>&1)"
  exit_code=$?
}

# check <description> <expected exit code> [+must-contain | -must-not-contain ...]
# Checks the last run's exit code and output, AND that it cleaned up:
# no server process left, and its temp folder deleted.
check() {
  local description="$1" expected_exit="$2" problem="" text server_pid temp_folder
  shift 2
  [ "$exit_code" -eq "$expected_exit" ] || problem="exit $exit_code, expected $expected_exit"
  for expectation in "$@"; do
    text="${expectation#?}"
    case "$expectation" in
      +*) printf '%s' "$output" | grep -qF -- "$text" || problem="$problem; missing text: '$text'" ;;
      -*) printf '%s' "$output" | grep -qF -- "$text" && problem="$problem; unexpected text: '$text'" ;;
    esac
  done

  # The script prints the server's PID and its temp folder, so we can check
  # that both are really gone.
  server_pid="$(printf '%s\n' "$output" | sed -n 's/^Started the practice server (PID \([0-9]*\)).*/\1/p')"
  if [ -n "$server_pid" ] && kill -0 "$server_pid" 2>/dev/null; then
    problem="$problem; server PID $server_pid is still running"
  fi
  if pgrep -f "$SERVER_PROCESS_PATTERN" >/dev/null; then
    problem="$problem; a server process is still running: $(pgrep -af "$SERVER_PROCESS_PATTERN")"
  fi
  temp_folder="$(printf '%s\n' "$output" | sed -n 's/^Temp folder for this run: //p')"
  if [ -n "$temp_folder" ] && [ -e "$temp_folder" ]; then
    problem="$problem; temp folder $temp_folder was not deleted"
  fi

  if [ -z "$problem" ]; then
    echo "pass  $description"
    passed=$((passed + 1))
  else
    echo "FAIL  $description -- ${problem#; }"
    printf '%s\n' "$output" | sed 's/^/      /'
    failed=$((failed + 1))
  fi
}

# --- The client fails or does nothing -----------------------------------------

live_check false
check "a client that fails -> exit 1, server stopped, temp folder deleted" 1 \
  "+Started the practice server" "+client: FAILED (exit 1)" "+Stopped the practice server" \
  "-LIVE CHECK PASSED"

live_check true
check "a client that exits 0 but plays nothing -> the report check fails" 1 \
  "+client: passed" '+status: expected "sample exchange completed", got "in progress"' \
  "+report: FAILED" "-LIVE CHECK PASSED"

# --- What the client is given -------------------------------------------------
# This fake client checks what it received and prints only yes/no answers,
# never the file's contents, because the file holds real tokens.
live_check python -c '
import json, os, sys
arguments = sys.argv[1:]
assert arguments[0] == "--credentials", arguments
players = json.load(open(arguments[1]))["players"]
p01 = [player for player in players if player["station_id"] == "P01"]
assert len(p01) == 1 and p01[0]["token"], "no P01 token"
assert os.environ.get("PYTHONPATH") == "src", "PYTHONPATH is not src"
print("fake client: got a credentials file with a P01 token, and PYTHONPATH=src")
'
check "the client gets --credentials <fresh file with P01> and PYTHONPATH=src" 1 \
  "+fake client: got a credentials file with a P01 token, and PYTHONPATH=src" "+client: passed"

# --- The whole run passes ----------------------------------------------------
# No real client exists yet, so this fake one writes a passing report where
# the server keeps it. It only tests the script's own success path.
live_check python -c '
import json, pathlib, sys
report_file = pathlib.Path(sys.argv[2]).parent / "validation-report.json"
report_file.write_text(json.dumps({
    "status": "sample exchange completed",
    "last_completed_step": 10,
    "final_inventory": {"water": 28, "food": 31, "components": 31},
}))
'
check "client and report both pass -> exit 0" 0 \
  "+client: passed" "+check_report: PASSED" "+report: passed" "+LIVE CHECK PASSED"

# --- Interrupted part-way ------------------------------------------------------
# This fake client would keep "playing" for a whole minute, like a stuck
# client. An interrupted live check must stop it (and the server) within
# seconds instead of waiting for it. Its last argument is just a label, so
# we can spot any copy that's still running.
FAKE_CLIENT_LABEL="fake-stuck-client"
STUCK_CLIENT=(python3 -c 'import time; time.sleep(60)' "$FAKE_CLIENT_LABEL")
MAX_STOP_SECONDS=10

# check_stopped_quickly <how it was interrupted> <seconds it took to stop>
# Passes if the script ended within MAX_STOP_SECONDS and no fake client is
# still running.
check_stopped_quickly() {
  local how="$1" seconds="$2" problem=""
  [ "$seconds" -le "$MAX_STOP_SECONDS" ] || problem="took ${seconds}s, more than ${MAX_STOP_SECONDS}s"
  if pgrep -f "$FAKE_CLIENT_LABEL" >/dev/null; then
    problem="$problem; the fake client is still running"
  fi
  if [ -z "$problem" ]; then
    echo "pass  ...and after $how it stopped the client too, within ${seconds}s"
    passed=$((passed + 1))
  else
    echo "FAIL  after $how: ${problem#; }"
    failed=$((failed + 1))
  fi
}

# `kill` sends SIGTERM to the script alone. The client never receives it,
# so the script has to stop the client itself.
bash "$LIVE_CHECK" "${STUCK_CLIENT[@]}" >"$WORK/killed.log" 2>&1 &
live_check_pid=$!
for _ in $(seq 1 300); do   # wait up to 30 seconds for the client to start
  grep -q "Running the client" "$WORK/killed.log" && break
  sleep 0.1
done
sleep 1   # give the fake client a moment to really be running
started_at=$SECONDS
kill -TERM "$live_check_pid"
wait "$live_check_pid"
exit_code=$?
seconds=$((SECONDS - started_at))
output="$(cat "$WORK/killed.log")"
check "killed while the client runs -> exit 143, server stopped, temp folder deleted" 143 \
  "+Started the practice server" "+Stopped the client" "+Stopped the practice server" \
  "-LIVE CHECK PASSED"
check_stopped_quickly "kill" "$seconds"

# A real Ctrl+C comes from a terminal, which sends SIGINT to the programs
# running in it. Python's `pty` module gives the script a pretend terminal,
# so we can "press" Ctrl+C by writing its byte (\x03).
output="$(python3 - "$LIVE_CHECK" "${STUCK_CLIENT[@]}" <<'PY'
import os, pty, select, sys, time

pid, terminal = pty.fork()
if pid == 0:  # the child process: run the live check in the pretend terminal
    os.execvp("bash", ["bash", *sys.argv[1:]])

output = b""

def read_until(is_done, seconds):
    """Collect what the script prints until is_done(), it exits, or time runs out."""
    global output
    deadline = time.time() + seconds
    while not is_done() and time.time() < deadline:
        if select.select([terminal], [], [], 0.1)[0]:
            try:
                output += os.read(terminal, 4096)
            except OSError:  # the script ended and closed the terminal
                return

read_until(lambda: b"Running the client" in output, 30)
time.sleep(1)  # give the fake client a moment to really be running
pressed_at = time.time()
os.write(terminal, b"\x03")  # Ctrl+C
read_until(lambda: False, 90)  # read everything until the script ends
_, status = os.waitpid(pid, 0)
# Terminals end lines with \r\n; turn them into plain \n for the checks.
print(output.decode(errors="replace").replace("\r\n", "\n"))
print(f"SECONDS_TO_STOP={round(time.time() - pressed_at)}")
sys.exit(os.waitstatus_to_exitcode(status))
PY
)"
exit_code=$?
# (No "+Stopped the practice server" here: Ctrl+C reaches the server too,
# and it may stop itself first. check() still proves it's gone.)
check "Ctrl+C while the client runs -> exit 130, server stopped, temp folder deleted" 130 \
  "+Started the practice server" "+Stopped the client" "-LIVE CHECK PASSED"
check_stopped_quickly "Ctrl+C" "$(printf '%s\n' "$output" | sed -n 's/^SECONDS_TO_STOP=//p')"

# --- Something else already on the port ------------------------------------------
# If an old server were still running, the client would connect to IT with
# the new server's token. The script must refuse to start instead, and must
# not stop a process it didn't start.
python -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1 &
other_pid=$!
for _ in $(seq 1 100); do   # wait up to 10 seconds for it to listen
  [ -n "$(ss -Hltn "sport = :$PORT")" ] && break
  sleep 0.1
done
live_check true
check "port $PORT already in use -> refuses to start" 1 \
  "+already listening on port $PORT" "-Started the practice server"
if kill -0 "$other_pid" 2>/dev/null; then
  echo "pass  ...and it left the other program on port $PORT running"
  passed=$((passed + 1))
else
  echo "FAIL  it stopped a program it didn't start"
  failed=$((failed + 1))
fi
kill "$other_pid" 2>/dev/null
wait "$other_pid" 2>/dev/null

echo
echo "$passed passed, $failed failed"
[ "$failed" -eq 0 ]
