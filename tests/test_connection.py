"""Tests for the binary, authenticated WebSocket transport."""

import pytest

from bazaar_client.connection import SUBPROTOCOL, connect
from bazaar_client.credentials import Credentials
from bazaar_client.errors import ConnectionFailed, ConnectionLost, ProtocolViolation
from fake_server.server import FakeServer, Mode


def credentials(server, *, station_id="P01", token=None):
    return Credentials(station_id, server.run_id, token or server.token)


@pytest.mark.asyncio
async def test_connect_authenticates_confirms_subprotocol_and_exchanges_bytes():
    async with FakeServer() as server:
        connection = await connect(server.url, credentials(server))
        try:
            first = await connection.receive()
            assert isinstance(first, bytes)
            with pytest.raises(TypeError, match="bytes"):
                await connection.send("text is unsafe")
        finally:
            await connection.close()


@pytest.mark.asyncio
async def test_connect_rejects_unconfirmed_subprotocol_and_closes_socket():
    async with FakeServer(mode=Mode.WRONG_SUBPROTOCOL) as server:
        with pytest.raises(ConnectionFailed, match=SUBPROTOCOL):
            await connect(server.url, credentials(server))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("station_id", "token", "status"),
    [("P01", "wrong-token", "401"), ("P02", None, "400")],
)
async def test_connect_translates_handshake_failures_without_leaking_token(
    station_id, token, status
):
    async with FakeServer() as server:
        selected_token = server.p02_token if station_id == "P02" else token
        supplied = credentials(server, station_id=station_id, token=selected_token)

        with pytest.raises(ConnectionFailed, match=status) as caught:
            await connect(server.url, supplied)

        assert supplied.token not in str(caught.value)


@pytest.mark.asyncio
async def test_receive_rejects_text_frames():
    async with FakeServer(mode=Mode.TEXT) as server:
        connection = await connect(server.url, credentials(server))
        try:
            assert isinstance(await connection.receive(), bytes)
            with pytest.raises(ProtocolViolation, match="text"):
                await connection.receive()
        finally:
            await connection.close()


@pytest.mark.asyncio
async def test_closed_connection_translates_send_and_receive_failures():
    async with FakeServer() as server:
        connection = await connect(server.url, credentials(server))
        await connection.receive()
        await connection.close()
        await connection.close()  # close is deliberately idempotent

        with pytest.raises(ConnectionLost):
            await connection.receive()
        with pytest.raises(ConnectionLost):
            await connection.send(b"message")
