#!/usr/bin/env bash
set -euo pipefail

python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
mkdir -p generated
touch generated/__init__.py
protoc --python_out=generated --proto_path=starter starter/bazaar.proto

echo "Setup complete. Open two terminals and run:"
echo "  bash scripts/start-server.sh"
echo "  .venv/bin/python client/connect.py"
