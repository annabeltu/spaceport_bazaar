"""
The WebSocket connection to the server (contract by package A, body by package G).

The spec's "Connect your client": send `Authorization: Bearer <token>` and
request the `bazaar.protobuf.v2` subprotocol, check the server confirms it,
then exchange one binary protobuf message per WebSocket message. Ping, pong
and close are connection controls that the `websockets` library handles, so
they never reach our code.

Library pointers: `from websockets.asyncio.client import connect`. After
connecting, `ws.subprotocol` says which subprotocol the server chose.
"""
from websockets.asyncio.client import connect as websocket_connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException

from bazaar_client.credentials import Credentials
from bazaar_client.errors import ConnectionFailed, ConnectionLost, ProtocolViolation

# The message format the spec requires. The server must confirm exactly this.
SUBPROTOCOL = "bazaar.protobuf.v2"

# Where the practice server listens. The spec runs the client and the server
# in the same container, so the address is always local.
DEFAULT_URL = "ws://127.0.0.1:3001/ws"


class Connection:
    """One open WebSocket session with the server.

    Only connect() creates these. What a Connection holds inside (the
    `websockets` connection object, for example) is package G's choice.
    """

    def __init__(self, websocket) -> None:
        self._websocket = websocket

    async def send(self, data: bytes) -> None:
        """Send `data` as ONE binary WebSocket message.

        Accepts only bytes and raises TypeError for anything else. That's on
        purpose: the only way to get bytes is guards.check(), so a protobuf
        message that skipped the checks can't be sent by mistake.
        Raises ConnectionLost (from bazaar_client.errors) if the connection has
        closed.
        """
        if not isinstance(data, bytes):
            raise TypeError("Connection.send() accepts bytes only.")
        try:
            await self._websocket.send(data)
        except ConnectionClosed as error:
            raise ConnectionLost("The WebSocket connection closed while sending.") from error

    async def receive(self) -> bytes:
        """Wait for the next message from the server and return its bytes.

        Raises ProtocolViolation for a text frame (the spec only sends binary),
        and ConnectionLost when the connection closes or drops.
        """
        try:
            frame = await self._websocket.recv()
        except ConnectionClosed as error:
            raise ConnectionLost("The WebSocket connection closed while receiving.") from error
        if not isinstance(frame, bytes):
            raise ProtocolViolation("Server sent a text frame; binary Protobuf was expected.")
        return frame

    async def close(self) -> None:
        """Close the connection. Safe to call more than once."""
        await self._websocket.close()


async def connect(url: str, credentials: Credentials) -> Connection:
    """Open a WebSocket to `url` as credentials.station_id.

    Sends `Authorization: Bearer <token>` and requests SUBPROTOCOL, and nothing
    else. Raises ConnectionFailed (from bazaar_client.errors), whose message
    never contains the token, when:
    - the server answers HTTP 401 (it rejected the token: was the server
      restarted since the credentials were read?),
    - the server answers HTTP 400 (wrong station or message format),
    - the server doesn't confirm SUBPROTOCOL (the connection is closed first), or
    - nothing is listening at `url`.
    """
    try:
        websocket = await websocket_connect(
            url,
            additional_headers={"Authorization": f"Bearer {credentials.token}"},
            subprotocols=[SUBPROTOCOL],
        )
    except InvalidStatus as error:
        status = error.response.status_code
        if status == 401:
            message = (
                "Server rejected the credentials with HTTP 401; restart or reread "
                "the server's credentials file."
            )
        elif status == 400:
            message = (
                "Server rejected the connection with HTTP 400; check the station, "
                "URL, and protobuf subprotocol."
            )
        else:
            message = f"Server rejected the WebSocket connection with HTTP {status}."
        raise ConnectionFailed(message) from error
    except (OSError, TimeoutError, WebSocketException) as error:
        raise ConnectionFailed(f"Could not connect to the Bazaar server at {url}.") from error

    if websocket.subprotocol != SUBPROTOCOL:
        await websocket.close()
        raise ConnectionFailed(
            f"Server did not confirm the required {SUBPROTOCOL} subprotocol."
        )
    return Connection(websocket)
