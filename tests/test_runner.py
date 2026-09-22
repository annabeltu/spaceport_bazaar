"""Integration tests for the runner and its command line."""

import pytest

from bazaar_client.__main__ import parse_args
from bazaar_client.connection import DEFAULT_URL
from bazaar_client.runner import RunOptions, run
from fake_server.server import FakeServer, Mode


def options(tmp_path, server, *, dry_run=False):
    credentials = tmp_path / "validation-credentials.json"
    server.write_credentials(credentials)
    return RunOptions(
        credentials_path=credentials,
        url=server.url,
        dry_run=dry_run,
        log_file=tmp_path / "client.log",
    )


@pytest.mark.asyncio
async def test_runner_completes_exchange_and_logs_every_message(tmp_path):
    async with FakeServer() as server:
        selected = options(tmp_path, server)
        result = await run(selected)

    lines = selected.log_file.read_text().splitlines()
    assert result == 0
    assert sum(" SENT " in line for line in lines) == 8
    assert sum(" RECEIVED " in line for line in lines) == 16
    assert len(server.received) == 8


@pytest.mark.asyncio
async def test_runner_reconnects_after_one_drop(tmp_path):
    async with FakeServer(mode=Mode.DROP, trigger_after_messages=1) as server:
        result = await run(options(tmp_path, server))

    assert result == 0


@pytest.mark.asyncio
async def test_runner_stops_on_invalid_server_bytes(tmp_path):
    async with FakeServer(mode=Mode.GARBAGE) as server:
        result = await run(options(tmp_path, server))

    assert result == 1


@pytest.mark.asyncio
async def test_dry_run_sends_nothing(tmp_path):
    async with FakeServer() as server:
        result = await run(options(tmp_path, server, dry_run=True))

    assert result == 0
    assert server.received == []


def test_parse_args_builds_options():
    parsed = parse_args(
        ["--credentials", "credentials.json", "--dry-run", "--log-file", "client.log"]
    )

    assert parsed.credentials_path.name == "credentials.json"
    assert parsed.url == DEFAULT_URL
    assert parsed.dry_run is True
    assert parsed.log_file.name == "client.log"


def test_parse_args_requires_credentials():
    with pytest.raises(SystemExit) as caught:
        parse_args([])

    assert caught.value.code == 2
