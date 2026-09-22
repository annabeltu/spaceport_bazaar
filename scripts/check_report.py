#!/usr/bin/env python3
"""
Check the practice server's report: did the live run pass?

Usage:
    python scripts/check_report.py PATH_TO_REPORT

The practice server keeps a JSON report of our client's progress (the file
given to its --report option). The spec says a finished exercise shows:

    status: "sample exchange completed"
    last_completed_step: 10
    final_inventory: { "water": 28, "food": 31, "components": 31 }

Exit codes:
    0  the report shows all three: the run passed
    1  the report is readable, but fields are wrong or missing (each is listed)
    2  there's no usable report: missing file, not JSON, or a usage mistake

"The client stopped at step 7" and "there's no report at all" need
different fixes, which is why they get different exit codes. (2 is also
what argparse uses for a usage mistake.)

Only the three fields above, plus the server's own `mismatch` note, are
ever printed. The report shouldn't contain secrets, but never echoing
anything else means we don't have to rely on that. Point this script at the
report only, never at the credentials file.
"""
import argparse
import json
import sys
from pathlib import Path

# What the spec says a finished practice run's report contains (end of step 10).
EXPECTED_FIELDS = {
    "status": "sample exchange completed",
    "last_completed_step": 10,
    "final_inventory": {"water": 28, "food": 31, "components": 31},
}

EXIT_PASSED = 0
EXIT_REPORT_FAILED = 1
EXIT_CANNOT_READ = 2


class ReportUnreadable(Exception):
    """There's no usable report to check. The message says why."""


def load_report(path: Path) -> dict:
    """Read the report file and return it as a dict.

    Raises ReportUnreadable with a plain-English reason if that's impossible.
    (`from None` hides Python's internal error chain, so the person running
    this sees one clear sentence instead of a traceback.)
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ReportUnreadable(
            f"{path}: report not found. The server writes it as soon as it "
            "starts, so check that the server started and the path is right."
        ) from None
    except IsADirectoryError:
        raise ReportUnreadable(f"{path} is a folder, not a report file.") from None
    except UnicodeDecodeError:
        # Python's own message for this quotes a byte of the file, so we
        # use a fixed message instead: nothing from the file gets printed.
        raise ReportUnreadable(
            f"{path} is not UTF-8 text, so it can't be a report."
        ) from None
    except OSError as error:
        raise ReportUnreadable(f"could not read {path}: {error}") from None

    try:
        report = json.loads(text)
    except json.JSONDecodeError as error:
        # The error only says WHERE parsing failed (line and column), never
        # the file's contents.
        raise ReportUnreadable(
            f"{path} is not valid JSON ({error}). An empty or cut-off file "
            "usually means the server was stopped while writing it."
        ) from None

    if not isinstance(report, dict):
        raise ReportUnreadable(
            f"{path} should hold a JSON object ({{ ... }}) at the top level."
        )
    return report


def as_json(value) -> str:
    """Show a value the way it's written in JSON: "text", 10, null, {...}."""
    return json.dumps(value)


def is_same_json(actual, expected) -> bool:
    """Whether two values are equal AND are the same kind of JSON value.

    Plain `==` is too forgiving here: Python says 10 == 10.0 and 1 == True.
    Comparing their JSON text is strict, because as_json(10) is "10" but
    as_json(10.0) is "10.0". sort_keys makes key order not matter.
    """
    return json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)


def find_differences(report: dict) -> list[str]:
    """One line for each expected field that is missing or has the wrong value."""
    differences = []
    for field, expected in EXPECTED_FIELDS.items():
        if field not in report:
            differences.append(f"{field}: missing (expected {as_json(expected)})")
        elif not is_same_json(report[field], expected):
            differences.append(
                f"{field}: expected {as_json(expected)}, got {as_json(report[field])}"
            )
    return differences


def parse_arguments() -> Path:
    parser = argparse.ArgumentParser(
        description="Check that the practice server's report shows a passing run."
    )
    parser.add_argument(
        "report", type=Path, help="the report file (the server's --report option)"
    )
    return parser.parse_args().report


def main() -> int:
    """Check the report and return the process exit code."""
    report_path = parse_arguments()

    try:
        report = load_report(report_path)
    except ReportUnreadable as problem:
        print(f"check_report: ERROR -- {problem}", file=sys.stderr)
        return EXIT_CANNOT_READ

    differences = find_differences(report)
    if not differences:
        print(
            "check_report: PASSED -- sample exchange completed at step 10, "
            "final inventory water 28, food 31, components 31."
        )
        return EXIT_PASSED

    print(
        f"check_report: FAILED -- {report_path} does not show a finished practice run:",
        file=sys.stderr,
    )
    for line in differences:
        print(f"  {line}", file=sys.stderr)

    # The server fills `mismatch` when a command arrives out of order. It's
    # the best clue to what went wrong, so show it when it's there.
    mismatch = report.get("mismatch")
    if mismatch is not None:
        print(f"  server's mismatch note: {as_json(mismatch)}", file=sys.stderr)
    return EXIT_REPORT_FAILED


if __name__ == "__main__":
    sys.exit(main())
