#!/usr/bin/env bash
# Tests for scripts/hooks/pre-commit.
#
# Run:  bash tests/hooks/test_pre_commit.sh
#
# Works on a throwaway clone in a temp directory with FAKE tokens, so your
# real repo and real credentials are never touched. It tests the hook as it
# is in your working tree right now, so run it before committing hook edits.
#
# Written for bash 3.2 (macOS) like the hook itself.
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT   # always clean up the temp clone, even on failure

# Setup must succeed completely. If any step fails, stop here: a test run
# with a broken setup would report meaningless passes and failures.
setup_failed() { echo "SETUP FAILED: $1" >&2; exit 2; }
git clone -q "$REPO_ROOT" "$WORK/repo"               || setup_failed "clone"
cd "$WORK/repo"                                      || setup_failed "cd"
mkdir -p scripts/hooks                               || setup_failed "mkdir"
cp "$REPO_ROOT/scripts/hooks/pre-commit" scripts/hooks/pre-commit \
                                                     || setup_failed "copy hook"
git config core.hooksPath scripts/hooks              || setup_failed "hooksPath"
git config user.name "hook-test"                     || setup_failed "user.name"
git config user.email "hook-test@example.invalid"    || setup_failed "user.email"

random_token() {
  # 64 random hex characters, shaped like the server's tokens.
  head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
}
FAKE_TOKEN="$(random_token)"
FAKE_INSTRUCTOR_TOKEN="$(random_token)"
printf '{"instructor_token":"%s","players":[{"token":"%s","run_id":"r1","station_id":"P01"}]}\n' \
  "$FAKE_INSTRUCTOR_TOKEN" "$FAKE_TOKEN" > validation-credentials.json

passed=0
failed=0

# expect <blocked|allowed> <description>
# Tries to commit whatever is currently staged, checks the outcome, then
# resets the clone so the next test starts clean.
expect() {
  local wanted="$1" description="$2" output exit_code outcome
  output="$(git commit -q -m "test: $description" 2>&1)"
  exit_code=$?
  if [ "$exit_code" -eq 0 ]; then outcome="allowed"; else outcome="blocked"; fi

  if printf '%s' "$output" | grep -qF -e "$FAKE_TOKEN" -e "$FAKE_INSTRUCTOR_TOKEN"; then
    echo "FAIL  $description  (the hook's own output leaked a token)"
    failed=$((failed + 1))
  elif [ "$outcome" = "$wanted" ]; then
    echo "pass  $description -> $outcome"
    passed=$((passed + 1))
  else
    echo "FAIL  $description -> $outcome (expected $wanted)"
    printf '%s\n' "$output" | sed 's/^/      /'
    failed=$((failed + 1))
  fi

  # Reset: unstage, discard edits, delete untracked files. The hook is
  # committed below, so `git clean` can't delete it between tests.
  git reset -q HEAD -- . 2>/dev/null
  git checkout -q -- . 2>/dev/null
  git clean -qfd >/dev/null 2>&1
}

# The hook contains regex patterns that look a bit like secrets.
# Make sure it doesn't block its own commit.
git add scripts/hooks/pre-commit
expect allowed "committing the hook itself"

echo "print('hello')" > ok.py && git add ok.py
expect allowed "ordinary code"

git add -f validation-credentials.json
expect blocked "force-adding validation-credentials.json"

printf 'debug notes\nmy token was %s\n' "$FAKE_TOKEN" > notes.txt && git add notes.txt
expect blocked "a raw live token pasted into notes"

printf 'x = "%s"\n' "$FAKE_INSTRUCTOR_TOKEN" > leak.py && git add leak.py
expect blocked "the instructor token pasted into code"

printf 'headers = {"Authorization": "Bearer %s"}\n' "abcdefghijklmnopqrstuvwxyz0123456789" > client.py && git add client.py
expect blocked "a hardcoded Bearer header"

printf '{"token": "%s"}\n' "zyxwvutsrqponmlkjihgfedcba987654" > saved.json && git add saved.json
expect blocked "a JSON token literal in another file"

printf '%s  some/file.bin\n' "$(random_token)" > more.sha256 && git add more.sha256
expect allowed "a 64-hex checksum line (must not be a false positive)"

printf 'token = entry["token"]\nheaders = {"Authorization": f"Bearer {token}"}\n' > reader.py && git add reader.py
expect allowed "code that reads the token from the file (must not be a false positive)"

rm validation-credentials.json
echo "print('still fine')" > ok2.py && git add ok2.py
expect allowed "no credentials file present at all"

echo
echo "$passed passed, $failed failed"
[ "$failed" -eq 0 ]
