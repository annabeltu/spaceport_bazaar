"""Rung-2 checks: the assembled client against every fake-server mode."""

import pytest

from bazaar_client.runner import RunOptions, run
from fake_server.server import FakeServer, Mode


def options(tmp_path, server):
    credentials = tmp_path / "validation-credentials.json"
    log_file = tmp_path / "client.log"
    server.write_credentials(credentials)
    return RunOptions(credentials, server.url, False, log_file)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        Mode.WRONG_SUBPROTOCOL,
        Mode.GARBAGE,
        Mode.TEXT,
        Mode.CLOSE_SESSION,
        Mode.NEW_RUN_ID,
    ],
)
async def test_terminal_server_misbehavior_stops_cleanly_without_leaking_token(
    tmp_path, mode
):
    async with FakeServer(mode=mode) as server:
        selected = options(tmp_path, server)
        result = await run(selected)

    assert result == 1
    log = selected.log_file.read_text()
    assert server.token not in log
    assert "Traceback" not in log


@pytest.mark.asyncio
async def test_one_dropped_connection_recovers_and_finishes_without_token(tmp_path):
    async with FakeServer(mode=Mode.DROP, trigger_after_messages=3) as server:
        selected = options(tmp_path, server)
        result = await run(selected)

    assert result == 0
    log = selected.log_file.read_text()
    assert "reconnecting" in log
    assert server.token not in log
    assert sum(" SENT " in line for line in log.splitlines()) >= 8
    assert sum(" RECEIVED " in line for line in log.splitlines()) >= 16
