"""Tests for reading the practice server's short-lived credentials."""

import json

import pytest

from bazaar_client.credentials import Credentials, load_credentials
from bazaar_client.errors import CredentialsError


def write_credentials(path, *, token="fake-token-one", run_id="run-1"):
    path.write_text(
        json.dumps(
            {
                "players": [
                    {"station_id": "P02", "run_id": run_id, "token": "other-token"},
                    {"station_id": "P01", "run_id": run_id, "token": token},
                ]
            }
        )
    )


def test_load_credentials_selects_station_and_hides_token(tmp_path):
    path = tmp_path / "validation-credentials.json"
    write_credentials(path)

    credentials = load_credentials(path, "P01")

    assert credentials == Credentials("P01", "run-1", "fake-token-one")
    assert "fake-token-one" not in repr(credentials)


def test_load_credentials_reads_fresh_file_each_time(tmp_path):
    path = tmp_path / "validation-credentials.json"
    write_credentials(path, token="first-token", run_id="run-1")
    first = load_credentials(path, "P01")
    write_credentials(path, token="second-token", run_id="run-2")

    second = load_credentials(path, "P01")

    assert first.run_id == "run-1"
    assert second == Credentials("P01", "run-2", "second-token")


@pytest.mark.parametrize(
    ("contents", "words"),
    [
        ({"players": []}, "P01"),
        ({"players": [{"station_id": "P01", "run_id": "run-1"}]}, "token"),
        ({"players": [{"station_id": "P01", "token": "secret"}]}, "run_id"),
    ],
)
def test_load_credentials_rejects_missing_entries_or_fields(tmp_path, contents, words):
    path = tmp_path / "validation-credentials.json"
    path.write_text(json.dumps(contents))

    with pytest.raises(CredentialsError, match=words) as caught:
        load_credentials(path, "P01")

    assert "secret" not in str(caught.value)


def test_load_credentials_explains_missing_file(tmp_path):
    path = tmp_path / "validation-credentials.json"

    with pytest.raises(CredentialsError, match="start the server"):
        load_credentials(path, "P01")


def test_load_credentials_wraps_invalid_json_without_echoing_it(tmp_path):
    path = tmp_path / "validation-credentials.json"
    path.write_text('{"token": "do-not-echo"')

    with pytest.raises(CredentialsError, match="valid JSON") as caught:
        load_credentials(path, "P01")

    assert "do-not-echo" not in str(caught.value)
