#!/usr/bin/env bash
# Generate Python code from bazaar.proto.
# Re-run this whenever bazaar.proto changes.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PROTO_DIR="bazaar-protobuf-starter-linux"
OUT_DIR="src/generated"

mkdir -p "$OUT_DIR"

# We run protoc through grpcio-tools instead of installing protoc separately,
# so the compiler version always matches the pinned protobuf runtime.
#   --python_out  writes bazaar_pb2.py (the code you import)
#   --pyi_out     writes bazaar_pb2.pyi (type hints, so your editor can
#                 autocomplete message fields)
python -m grpc_tools.protoc \
  --proto_path="$PROTO_DIR" \
  --python_out="$OUT_DIR" \
  --pyi_out="$OUT_DIR" \
  "$PROTO_DIR/bazaar.proto"

echo "Generated $OUT_DIR/bazaar_pb2.py"
