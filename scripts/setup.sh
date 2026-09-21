#!/usr/bin/env bash
# One-time project setup. Safe to re-run.
# The dev container runs this automatically when it's first created.
#
#   set -e          stop at the first error instead of carrying on
#   set -u          treat an unset variable as an error (catches typos)
#   set -o pipefail a failure anywhere in a pipeline fails the whole thing
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

STARTER_DIR="bazaar-protobuf-starter-linux"
STARTER_TARBALL="artifacts/bazaar-starter-linux.tar.gz"

echo "==> 1/5 Checking the downloaded tarball's fingerprint"
bash scripts/verify_checksums.sh tarball

echo "==> 2/5 Unpacking the starter"
if [ -d "$STARTER_DIR" ]; then
  echo "Already unpacked at $STARTER_DIR -- skipping."
else
  tar -xzf "$STARTER_TARBALL"
fi

echo "==> 3/5 Checking the unpacked files' fingerprints"
# Runs even when we skipped unpacking, so a file changed on disk since the
# last setup still gets caught.
bash scripts/verify_checksums.sh starter

echo "==> 4/5 Generating Python code from bazaar.proto"
bash scripts/gen_proto.sh

echo "==> 5/5 Turning on the secret-blocking git hook"
git config core.hooksPath scripts/hooks
echo "core.hooksPath = $(git config core.hooksPath)"

echo
echo "Setup complete. Start the practice server with: bash scripts/run_server.sh"
