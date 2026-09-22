"""
Tests for scripts/check_report.py, the pass/fail judge for a live run.

The practice server writes a JSON report of our client's progress. A live
run only counts as passing if that report says the exchange completed, at
step 10, with the spec's final inventory.

We run the script as a separate program (a "subprocess") instead of
importing it, because scripts/ isn't on Python's import path. That also
tests it exactly the way run_live_check.sh uses it: exit code plus output.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECK_REPORT = REPO_ROOT / "scripts" / "check_report.py"

# The script's exit codes, so each test says which outcome it expects.
EXIT_PASSED = 0
EXIT_REPORT_FAILED = 1  # the report is readable, but the run didn't pass
EXIT_CANNOT_READ = 2  # no usable report at all (missing, not JSON, ...)

FINAL_INVENTORY = {"water": 28, "food": 31, "components": 31}


# --- Sample reports ---------------------------------------------------------
# Each helper returns a NEW dict, so one test's changes never leak into
# another test.


def started_report():
    """The report exactly as the real server writes it on startup.

    Observed on 2026-09-21 (spaceport 0.1.0) by starting the server with no
    client connected. Only the run_id is made up. There is no token in it:
    tokens only ever appear in the credentials file.
    """
    return {
        "profile": "message-samples-1",
        "run_id": "sample-0123456789abcdef",
        "station_id": "P01",
        "status": "in progress",
        "last_completed_step": 0,
        "inbound": [],
        "outbound_written": [],
        "mismatch": None,
        "final_inventory": None,
        "interpretation": (
            "Records successful server writes and validated inbound samples. "
            "It does not prove client decoding, display, or full "
            "C01–C20/S01–S24 conformance."
        ),
    }


def completed_report():
    """What a finished practice run's report should look like.

    The three checked values are the ones the spec names at the end of
    step 10. In a real completed run, `inbound` and `outbound_written`
    also list the messages; package K records real ones. The checker
    ignores those lists, so leaving them empty here doesn't matter.
    """
    return {
        **started_report(),
        "status": "sample exchange completed",
        "last_completed_step": 10,
        "final_inventory": dict(FINAL_INVENTORY),
    }


def without_field(report, field):
    """A copy of `report` with one field left out."""
    return {key: value for key, value in report.items() if key != field}


def with_inventory(**changes):
    """A completed report whose final_inventory has some amounts changed."""
    return {
        **completed_report(),
        "final_inventory": {**FINAL_INVENTORY, **changes},
    }


# --- Helpers to run the script ------------------------------------------------


def write_report(tmp_path, report):
    """Save a report dict as JSON, the way the server does, and return its path."""
    path = tmp_path / "validation-report.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def run_check(*args):
    """Run check_report.py with these arguments and capture what it prints."""
    return subprocess.run(
        [sys.executable, str(CHECK_REPORT), *map(str, args)],
        capture_output=True,
        text=True,
        check=False,  # a non-zero exit is an expected outcome here, not an error
    )


def assert_no_crash(result):
    """Bad input should get a friendly message, never a Python traceback."""
    assert "Traceback" not in result.stderr


# --- A passing report ---------------------------------------------------------


def test_completed_report_passes(tmp_path):
    result = run_check(write_report(tmp_path, completed_report()))

    assert result.returncode == EXIT_PASSED
    assert "PASSED" in result.stdout
    assert result.stderr == ""


def test_field_order_and_layout_do_not_matter(tmp_path):
    # JSON objects have no meaningful order, and the server may change its
    # indentation. Only the values count.
    report = completed_report()
    reordered = dict(reversed(list(report.items())))
    path = tmp_path / "compact.json"
    path.write_text(json.dumps(reordered, separators=(",", ":")), encoding="utf-8")

    assert run_check(path).returncode == EXIT_PASSED


# --- Reports that show a failed or unfinished run -------------------------------


def test_report_from_a_server_nobody_finished_fails_on_all_three_fields(tmp_path):
    # This is what you'd see if the client never connected, or crashed early.
    result = run_check(write_report(tmp_path, started_report()))

    assert result.returncode == EXIT_REPORT_FAILED
    assert "status" in result.stderr
    assert '"in progress"' in result.stderr
    assert "last_completed_step" in result.stderr
    assert "final_inventory" in result.stderr
    assert "null" in result.stderr  # final_inventory is null until the end
    assert_no_crash(result)


def test_wrong_status_fails_and_shows_expected_and_actual(tmp_path):
    report = {**completed_report(), "status": "scenario mismatch"}

    result = run_check(write_report(tmp_path, report))

    assert result.returncode == EXIT_REPORT_FAILED
    assert "status" in result.stderr
    assert '"sample exchange completed"' in result.stderr  # what we wanted
    assert '"scenario mismatch"' in result.stderr  # what we got
    # The other two fields are fine, so they aren't listed as problems.
    assert "last_completed_step:" not in result.stderr
    assert "final_inventory:" not in result.stderr


def test_mismatch_details_are_shown_when_the_server_recorded_one(tmp_path):
    # The server fills `mismatch` when a command arrives out of order. Its
    # exact shape isn't known yet (package K records a real one), so this
    # placeholder only checks that whatever is there gets printed.
    mismatch = {"placeholder": "package K records the real shape"}
    report = {**completed_report(), "status": "scenario mismatch", "mismatch": mismatch}

    result = run_check(write_report(tmp_path, report))

    assert result.returncode == EXIT_REPORT_FAILED
    assert "mismatch" in result.stderr
    assert "package K records the real shape" in result.stderr


def test_wrong_step_fails(tmp_path):
    report = {**completed_report(), "last_completed_step": 9}

    result = run_check(write_report(tmp_path, report))

    assert result.returncode == EXIT_REPORT_FAILED
    assert "last_completed_step: expected 10, got 9" in result.stderr
    assert "status:" not in result.stderr


@pytest.mark.parametrize("resource", ["water", "food", "components"])
def test_wrong_amount_of_any_resource_fails(tmp_path, resource):
    report = with_inventory(**{resource: FINAL_INVENTORY[resource] - 1})

    result = run_check(write_report(tmp_path, report))

    assert result.returncode == EXIT_REPORT_FAILED
    assert "final_inventory:" in result.stderr
    assert "status:" not in result.stderr


def test_inventory_from_before_the_gift_fails(tmp_path):
    # (28,31,30) is the inventory after step 5, before accepting the gift in
    # step 7. A client that stopped there must not pass.
    result = run_check(write_report(tmp_path, with_inventory(components=30)))

    assert result.returncode == EXIT_REPORT_FAILED


@pytest.mark.parametrize(
    "field", ["status", "last_completed_step", "final_inventory"]
)
def test_missing_field_fails_and_says_it_is_missing(tmp_path, field):
    report = without_field(completed_report(), field)

    result = run_check(write_report(tmp_path, report))

    assert result.returncode == EXIT_REPORT_FAILED
    assert f"{field}: missing" in result.stderr
    assert_no_crash(result)


@pytest.mark.parametrize(
    "field, lookalike",
    [
        # Each of these would pass a plain Python `==` check, which is why
        # the script compares types too.
        ("last_completed_step", "10"),  # a string, not a number
        ("last_completed_step", 10.0),  # 10.0 == 10 in Python
        ("final_inventory", {"water": "28", "food": "31", "components": "31"}),
        ("final_inventory", {"water": 28.0, "food": 31, "components": 31}),
    ],
)
def test_right_looking_value_of_the_wrong_type_fails(tmp_path, field, lookalike):
    report = {**completed_report(), field: lookalike}

    result = run_check(write_report(tmp_path, report))

    assert result.returncode == EXIT_REPORT_FAILED
    assert field in result.stderr


def test_inventory_missing_a_resource_fails(tmp_path):
    inventory = {"water": 28, "food": 31}  # no components
    report = {**completed_report(), "final_inventory": inventory}

    result = run_check(write_report(tmp_path, report))

    assert result.returncode == EXIT_REPORT_FAILED
    assert "final_inventory" in result.stderr


def test_failure_output_only_mentions_the_checked_fields(tmp_path):
    # The checker must never echo arbitrary report content. If something
    # sensitive ever ended up in another field, it stays off the screen.
    report = {**started_report(), "some_other_field": "DO-NOT-PRINT-THIS"}

    result = run_check(write_report(tmp_path, report))

    assert result.returncode == EXIT_REPORT_FAILED
    assert "DO-NOT-PRINT-THIS" not in result.stdout + result.stderr


# --- No usable report at all ---------------------------------------------------


def test_missing_file_gives_a_clear_error(tmp_path):
    missing = tmp_path / "no-such-report.json"

    result = run_check(missing)

    assert result.returncode == EXIT_CANNOT_READ
    assert "no-such-report.json" in result.stderr
    assert "not found" in result.stderr
    assert_no_crash(result)


@pytest.mark.parametrize(
    "contents",
    [
        "",  # empty file
        '{"status": "sample exchange completed", ',  # cut off mid-write
        "status: sample exchange completed",  # not JSON at all
    ],
    ids=["empty", "truncated", "not-json"],
)
def test_invalid_json_gives_a_clear_error(tmp_path, contents):
    path = tmp_path / "validation-report.json"
    path.write_text(contents, encoding="utf-8")

    result = run_check(path)

    assert result.returncode == EXIT_CANNOT_READ
    assert "not valid JSON" in result.stderr
    assert_no_crash(result)


def test_json_that_is_not_an_object_gives_a_clear_error(tmp_path):
    path = tmp_path / "validation-report.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    result = run_check(path)

    assert result.returncode == EXIT_CANNOT_READ
    assert "JSON object" in result.stderr
    assert_no_crash(result)


def test_binary_file_gives_a_clear_error_without_echoing_its_bytes(tmp_path):
    path = tmp_path / "validation-report.json"
    path.write_bytes(b"\xff\xfe binary junk")

    result = run_check(path)

    assert result.returncode == EXIT_CANNOT_READ
    assert "not UTF-8 text" in result.stderr
    assert "0xff" not in result.stderr  # no piece of the file's contents
    assert_no_crash(result)


def test_a_folder_instead_of_a_file_gives_a_clear_error(tmp_path):
    result = run_check(tmp_path)

    assert result.returncode == EXIT_CANNOT_READ
    assert "is a folder" in result.stderr
    assert_no_crash(result)


def test_no_arguments_prints_usage():
    result = run_check()

    assert result.returncode == EXIT_CANNOT_READ
    assert "usage" in result.stderr.lower()
