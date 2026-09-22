# Working on Spaceport Bazaar

Our client for the COMP 590H Spaceport Bazaar project. The course's own
spec lives inside `artifacts/bazaar-starter-linux.tar.gz` and gets unpacked
to `bazaar-protobuf-starter-linux/README.md` -- read that first.

## Quick start

1. Install **Docker Desktop** and **VS Code** with the **Dev Containers** extension.
2. Clone this repo, open the folder in VS Code, and run
   **Dev Containers: Reopen in Container** from the Command Palette.
   The first open runs `scripts/setup.sh`, which checks the course files'
   fingerprints, unpacks the starter, generates the Python protobuf code,
   and turns on the secret-blocking git hook.
3. In a container terminal, start the practice server:

   ```bash
   bash scripts/run_server.sh
   ```

   Run your client in a second container terminal. Both must run inside
   the container: the server only accepts connections from its own machine.

## Safety rules

This setup is built around one idea: **make mistakes where they cost
nothing.** Here's what protects what.

**Course files are fingerprinted.** `checksums.sha256` records the exact
bytes of every file we got from the course. `setup.sh` and `run_server.sh`
refuse to continue if one is missing or changed, and the unpacked starter
opens read-only in VS Code. To get a clean copy:

```bash
rm -rf bazaar-protobuf-starter-linux && bash scripts/setup.sh
```

Never edit `checksums.sha256` just to make a check pass. If the course
publishes a new starter, read what changed first.

**Secrets never get committed.** The practice server writes access tokens
to `validation-credentials.json`. `.gitignore` hides it, and
`scripts/hooks/pre-commit` blocks any commit containing it or a token.
The container turns the hook on for you. **If you ever commit from your
own machine instead of the container, run this once:**

```bash
git config core.hooksPath scripts/hooks
```

Don't bypass it with `--no-verify`. If a token does get pushed, restart the
practice server -- that issues new tokens.

**This repo is public.** Anything pushed can be seen and copied, and git
history keeps it even after deletion.

**Everything else:** the container runs as the ordinary `vscode` user, not
root, and Python packages are pinned to exact versions in
`requirements.txt` -- upgrade on purpose, then rerun the tests.

## Running the tests

Inside the container:

```bash
pytest                                        # Python tests, about a second
bash tests/hooks/test_pre_commit.sh           # the secret-blocking hook
bash tests/scripts/test_verify_checksums.sh   # the fingerprint checks
bash tests/scripts/test_run_live_check.sh     # the live-check script (container only)
```

The hook and checksum test scripts also run on macOS. They only ever damage
throwaway copies in a temp directory, never your real files.

Tests marked `live` start the real practice server, so plain `pytest` skips
them to stay fast. Run them on purpose with `pytest -m live`.

## Checking a full run against the practice server

Inside the container, with nothing else using port 3001:

```bash
bash scripts/run_live_check.sh
```

It starts a fresh practice server in a temp folder, runs the client, checks
the server's report with `scripts/check_report.py`, and always stops the
server afterwards. Any arguments replace the client command, and
`--credentials <file>` is always added to the end.

## Branches

- Name branches `<your-name>-<short-description>`, e.g.
  `mason-initial-setup`. Open a pull request into `main`.
- To pull in updates from the course repo:

  ```bash
  git remote add upstream https://github.com/comp590h-26f/spaceport_bazaar.git
  git fetch upstream
  ```

## Roadmap

Code comments refer to these phases. Each step up costs more when
something goes wrong, so a change only moves up once it passes below.

| Phase | What |
|---|---|
| 0 | Safety baseline: checksums, secret hook, `.gitignore` |
| 1 | Dev container and scripts |
| 2 | Test harness: the spec's example messages as answer keys, plus tests of what protobuf does and doesn't catch |
| 3 | The client, layer by layer, with pre-send guards |
| 4 | A fake server to test failure cases on demand |
| 5 | Automated runs against the practice server |
| 6 | Hardening for the classroom game |
