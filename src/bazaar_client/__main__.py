"""
The command line (contract by package A, body by package I):

    PYTHONPATH=src python -m bazaar_client --credentials PATH [--url URL] [--dry-run] [--log-file PATH]

`python -m bazaar_client` runs this file.
"""
import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from bazaar_client.connection import DEFAULT_URL
from bazaar_client.runner import RunOptions
from bazaar_client.runner import run


def parse_args(argv: Sequence[str]) -> RunOptions:
    """RunOptions from the command-line arguments (without the program name).

    --credentials PATH   required: the server's validation-credentials.json
    --url URL            default: connection.DEFAULT_URL
    --dry-run            connect and read, but send nothing
    --log-file PATH      also write the log here (default: console only)

    Bad arguments print the usage and exit with status 2, argparse's standard
    behavior.
    """
    parser = argparse.ArgumentParser(description="Run the Spaceport Bazaar practice client.")
    parser.add_argument(
        "--credentials",
        required=True,
        type=Path,
        help="path to validation-credentials.json",
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="Bazaar WebSocket URL")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="connect and show the first command without sending it",
    )
    parser.add_argument("--log-file", type=Path, help="also write logs to this file")
    parsed = parser.parse_args(argv)
    return RunOptions(
        credentials_path=parsed.credentials,
        url=parsed.url,
        dry_run=parsed.dry_run,
        log_file=parsed.log_file,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Parse `argv` (default: sys.argv[1:]), run the client, and return the
    exit code from runner.run(). Uses asyncio.run() to run it."""
    selected = sys.argv[1:] if argv is None else argv
    return asyncio.run(run(parse_args(selected)))


# Only true when started with `python -m bazaar_client`, not when a test
# imports this file.
if __name__ == "__main__":
    sys.exit(main())
