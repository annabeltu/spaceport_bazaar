"""
The command line (contract by package A, body by package I):

    PYTHONPATH=src python -m bazaar_client --credentials PATH [--url URL] [--dry-run] [--log-file PATH]

`python -m bazaar_client` runs this file.
"""
import sys
from collections.abc import Sequence

from bazaar_client.runner import RunOptions


def parse_args(argv: Sequence[str]) -> RunOptions:
    """RunOptions from the command-line arguments (without the program name).

    --credentials PATH   required: the server's validation-credentials.json
    --url URL            default: connection.DEFAULT_URL
    --dry-run            connect and read, but send nothing
    --log-file PATH      also write the log here (default: console only)

    Bad arguments print the usage and exit with status 2, argparse's standard
    behavior.
    """
    raise NotImplementedError("package I")


def main(argv: Sequence[str] | None = None) -> int:
    """Parse `argv` (default: sys.argv[1:]), run the client, and return the
    exit code from runner.run(). Uses asyncio.run() to run it."""
    raise NotImplementedError("package I")


# Only true when started with `python -m bazaar_client`, not when a test
# imports this file.
if __name__ == "__main__":
    sys.exit(main())
