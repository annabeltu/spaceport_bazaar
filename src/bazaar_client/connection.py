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
from bazaar_client.credentials import Credentials

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

    async def send(self, data: bytes) -> None:
        """Send `data` as ONE binary WebSocket message.

        Accepts only bytes and raises TypeError for anything else. That's on
        purpose: the only way to get bytes is guards.check(), so a protobuf
        message that skipped the checks can't be sent by mistake.
        Raises ConnectionLost (from bazaar_client.errors) if the connection has
        closed.
        """
        raise NotImplementedError("package G")

    async def receive(self) -> bytes:
        """Wait for the next message from the server and return its bytes.

        Raises ProtocolViolation for a text frame (the spec only sends binary),
        and ConnectionLost when the connection closes or drops.
        """
        raise NotImplementedError("package G")

    async def close(self) -> None:
        """Close the connection. Safe to call more than once."""
        raise NotImplementedError("package G")


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
    raise NotImplementedError("package G")
