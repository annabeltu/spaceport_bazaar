#!/usr/bin/env bash
# Verify the course's downloaded files against the fingerprints pinned in
# checksums.sha256.
#
# Usage:
#   bash scripts/verify_checksums.sh tarball   # only the tarball (before unpacking)
#   bash scripts/verify_checksums.sh starter   # only the unpacked files
#   bash scripts/verify_checksums.sh all       # everything (the default)
#
# Why this exists: we run server binaries that were downloaded from the
# internet. A SHA-256 hash is a fingerprint of a file's exact bytes -- change
# one byte and the fingerprint changes completely. So if a file gets corrupted,
# or the course quietly publishes a new version, this fails loudly instead of
# letting us run something nobody has checked.
#
# If the course DOES publish a new starter on purpose: re-download it, read
# what changed, and only then update checksums.sha256. Never update the
# fingerprints just to make this script pass.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

CHECKSUM_FILE="checksums.sha256"
SCOPE="${1:-all}"

case "$SCOPE" in
  tarball) LINE_PATTERN='  artifacts/' ;;
  starter) LINE_PATTERN='  bazaar-protobuf-starter-linux/' ;;
  all)     LINE_PATTERN='  ' ;;
  *)
    echo "Usage: $0 [tarball|starter|all]" >&2
    exit 2
    ;;
esac

# Linux ships `sha256sum`; macOS ships `shasum` instead. Both understand
# the same checksum-file format.
if command -v sha256sum >/dev/null 2>&1; then
  HASH_COMMAND="sha256sum"
else
  HASH_COMMAND="shasum -a 256"
fi

# Pick out only the lines for the requested scope.
# (grep exits 1 when nothing matches; we check for that explicitly below
# rather than letting `set -e` stop the script with no explanation.)
SELECTED_LINES="$(grep -F -- "$LINE_PATTERN" "$CHECKSUM_FILE" || true)"
if [ -z "$SELECTED_LINES" ]; then
  echo "verify_checksums: no entries for '$SCOPE' in $CHECKSUM_FILE" >&2
  exit 1
fi

# --strict also fails on malformed lines, so a damaged checksum file
# can't silently verify nothing.
if ! printf '%s\n' "$SELECTED_LINES" | $HASH_COMMAND --check --strict -; then
  echo >&2
  echo "verify_checksums: CHECKSUM MISMATCH -- do not run these files." >&2
  echo "A file is missing or differs from the version we checked. See the" >&2
  echo "note at the top of scripts/verify_checksums.sh before changing anything." >&2
  exit 1
fi
