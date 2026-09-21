#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root/starter"

case "$(uname -m)" in
  aarch64|arm64)
    server=./spaceport-validate-linux-arm64
    ;;
  x86_64|amd64)
    server=./spaceport-validate-linux-x86_64
    ;;
  *)
    echo "Unsupported container architecture: $(uname -m)" >&2
    exit 1
    ;;
esac

echo "Starting $server on ws://127.0.0.1:3001/ws"
exec "$server" --codec protobuf "$@"
