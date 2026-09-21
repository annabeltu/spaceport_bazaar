#!/usr/bin/env bash
# Start the practice server, choosing the binary that matches this CPU.
#
# Run this in one terminal and your client in another -- both INSIDE the
# dev container, because the server only accepts connections from the same
# machine.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

STARTER_DIR="bazaar-protobuf-starter-linux"

# `uname -m` reports the CPU type. The starter includes one server program
# per CPU type, and running the wrong one fails with
# "cannot execute binary file".
CPU="$(uname -m)"
case "$CPU" in
  x86_64 | amd64)
    SERVER_BINARY="spaceport-validate-linux-x86_64"
    ;;
  aarch64 | arm64)
    SERVER_BINARY="spaceport-validate-linux-arm64"
    ;;
  *)
    echo "Unsupported CPU type: $CPU" >&2
    echo "The starter only includes Linux x86_64 and Linux arm64 servers." >&2
    exit 1
    ;;
esac

# Check the binary's fingerprint right before running it -- the moment it
# actually matters.
echo "Checking the server's fingerprint..."
bash scripts/verify_checksums.sh starter

echo "CPU: $CPU -> $SERVER_BINARY"
echo "Listening at ws://127.0.0.1:3001/ws"
echo "Writes validation-credentials.json and validation-report.json in $REPO_ROOT"
echo "Stop with Ctrl+C. Restarting starts a NEW exercise with new credentials."
echo

# `exec` replaces this script with the server, so Ctrl+C goes straight to it.
exec "./$STARTER_DIR/$SERVER_BINARY" --codec protobuf
