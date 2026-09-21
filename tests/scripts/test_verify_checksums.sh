#!/usr/bin/env bash
# Tests for scripts/verify_checksums.sh.
#
# Run:  bash tests/scripts/test_verify_checksums.sh
#
# Every case damages a throwaway COPY in a temp directory -- never the real
# files. Needs the starter unpacked (run scripts/setup.sh first).
# Written for bash 3.2 (macOS) like the script it tests.
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

STARTER="bazaar-protobuf-starter-linux"
if [ ! -d "$REPO_ROOT/$STARTER" ]; then
  echo "SETUP FAILED: $STARTER/ not found. Run: bash scripts/setup.sh" >&2
  exit 2
fi

passed=0
failed=0

# Make a fresh, undamaged copy of just what the script needs.
fresh_copy() {
  rm -rf "$WORK/copy" && mkdir -p "$WORK/copy/scripts"
  cp "$REPO_ROOT/checksums.sha256" "$WORK/copy/"
  cp "$REPO_ROOT/scripts/verify_checksums.sh" "$WORK/copy/scripts/"
  cp -R "$REPO_ROOT/artifacts" "$REPO_ROOT/$STARTER" "$WORK/copy/"
  chmod -R u+w "$WORK/copy"
}

# check <description> <expected exit code> <scope> [+must-contain | -must-not-contain ...]
# Runs the script in the current copy and checks exit code and output.
check() {
  local description="$1" expected_exit="$2" scope="$3" output exit_code problem=""
  shift 3
  output="$(bash "$WORK/copy/scripts/verify_checksums.sh" "$scope" 2>&1)"
  exit_code=$?
  [ "$exit_code" -eq "$expected_exit" ] || problem="exit $exit_code, expected $expected_exit"
  for expectation in "$@"; do
    text="${expectation#?}"
    case "$expectation" in
      +*) printf '%s' "$output" | grep -qF -- "$text" || problem="$problem; missing text: '$text'" ;;
      -*) printf '%s' "$output" | grep -qF -- "$text" && problem="$problem; unexpected text: '$text'" ;;
    esac
  done
  if [ -z "$problem" ]; then
    echo "pass  $description"
    passed=$((passed + 1))
  else
    echo "FAIL  $description -- ${problem#; }"
    printf '%s\n' "$output" | sed 's/^/      /'
    failed=$((failed + 1))
  fi
}

fresh_copy
check "everything intact" 0 all \
  "+$STARTER/spaceport-validate-linux-x86_64: OK" "-MISSING" "-CHANGED"

fresh_copy
check "unknown scope is a usage error" 2 everything "+Usage:"

fresh_copy
printf 'X' >> "$WORK/copy/$STARTER/spaceport-validate-linux-x86_64"
check "one byte added to the server -> CHANGED" 1 starter \
  "+CHANGED FILE(S)" "+spaceport-validate-linux-x86_64: FAILED" "+rm -rf $STARTER" "-MISSING"

fresh_copy
sed -i.bak 's/spaceport-validate-linux-x86_64` | Linux/spaceprt-validate-linux-x86_64` | Linux/' \
  "$WORK/copy/$STARTER/README.md" && rm "$WORK/copy/$STARTER/README.md.bak"
check "the 2026-09-21 README keystroke -> CHANGED, with the rebuild hint" 1 starter \
  "+CHANGED FILE(S)" "+README.md: FAILED" "+rm -rf $STARTER" "-MISSING"

fresh_copy
rm "$WORK/copy/$STARTER/spaceport-validate-linux-arm64"
check "an unused binary deleted -> MISSING, not a scary mismatch" 1 starter \
  "+MISSING FILE(S)" "+$STARTER/spaceport-validate-linux-arm64" "+rm -rf $STARTER" "-CHANGED" "-do not run"

fresh_copy
rm "$WORK/copy/artifacts/bazaar-starter-linux.tar.gz"
check "tarball deleted -> MISSING, with the git restore hint" 1 tarball \
  "+MISSING FILE(S)" "+git restore artifacts/" "-rm -rf $STARTER" "-CHANGED"

fresh_copy
printf 'X' >> "$WORK/copy/$STARTER/bazaar.proto"
check "tarball scope ignores damage to the unpacked starter" 0 tarball \
  "+artifacts/bazaar-starter-linux.tar.gz: OK" "-bazaar.proto"

fresh_copy
rm "$WORK/copy/$STARTER/README.md"
printf 'X' >> "$WORK/copy/$STARTER/bazaar.proto"
check "missing AND changed files -> missing is reported first" 1 starter \
  "+MISSING FILE(S)" "+README.md"

echo
echo "$passed passed, $failed failed"
[ "$failed" -eq 0 ]
