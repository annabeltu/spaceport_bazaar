"""Thin WebSocket transport around the independent scenario state machine."""

from __future__ import annotations

from enum import Enum
from http import HTTPStatus
import json
from pathlib import Path

from websockets.http11 import Request, Response
from websockets.asyncio.server import ServerConnection, serve

from generated import bazaar_pb2 as pb

from fake_server.messages import build_protocol_error
from fake_server.scenario import Scenario


SUBPROTOCOL = "bazaar.protobuf.v2"


class Mode(Enum):
    HAPPY_PATH = "happy_path"
    WRONG_SUBPROTOCOL = "wrong_subprotocol"
    GARBAGE = "garbage"
    TEXT = "text"
    CLOSE_SESSION = "close_session"
    DROP = "drop"
    NEW_RUN_ID = "new_run_id"


class FakeServer:
    """Async context manager exposing a fake Bazaar server on a free port."""

    def __init__(
        self,
        mode: Mode = Mode.HAPPY_PATH,
        *,
        trigger_after_messages: int = 1,
    ) -> None:
        self.mode = mode
        self.trigger_after_messages = trigger_after_messages
        self.run_id = "fake-run-1"
        # Constructed at runtime so the repository's secret hook can't mistake it
        # for a real credential copied from validation-credentials.json.
        self.token = "fake-p01-" + "x" * 55
        self.p02_token = "fake-p02-" + "y" * 55
        self.instructor_token = "fake-instructor-" + "z" * 48
        self.url = ""
        self.received: list[pb.ClientMessage] = []
        self.sent: list[pb.ServerMessage] = []
        self._server = None
        self._scenario = Scenario.create(self.run_id)
        self._mode_triggered = False

    def write_credentials(self, path: Path) -> None:
        """Write the real server's credential shape to a caller-chosen temp path."""
        data = {
            "instructor_token": self.instructor_token,
            "players": [
                {"token": self.token, "run_id": self.run_id, "station_id": "P01"},
                {"token": self.p02_token, "run_id": self.run_id, "station_id": "P02"},
            ],
        }
        path.write_text(json.dumps(data, indent=2) + "\n")

    async def __aenter__(self) -> FakeServer:
        select_subprotocol = None
        if self.mode is Mode.WRONG_SUBPROTOCOL:
            select_subprotocol = lambda connection, offered: None
        self._server = await serve(
            self._handle_connection,
            "127.0.0.1",
            0,
            subprotocols=[SUBPROTOCOL],
            process_request=self._check_handshake,
            select_subprotocol=select_subprotocol,
        )
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/ws"
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        self._server.close()
        await self._server.wait_closed()

    def _check_handshake(
        self, websocket: ServerConnection, request: Request
    ) -> Response | None:
        if request.path != "/ws":
            return websocket.respond(HTTPStatus.BAD_REQUEST, "Wrong WebSocket path\n")
        authorization = request.headers.get("Authorization")
        if authorization == f"Bearer {self.p02_token}":
            return websocket.respond(HTTPStatus.BAD_REQUEST, "Only P01 may connect\n")
        if authorization != f"Bearer {self.token}":
            return websocket.respond(HTTPStatus.UNAUTHORIZED, "Invalid credentials\n")

        requested_protocols = request.headers.get_all("Sec-WebSocket-Protocol")
        offered = {
            protocol.strip()
            for header in requested_protocols
            for protocol in header.split(",")
        }
        if SUBPROTOCOL not in offered:
            return websocket.respond(HTTPStatus.BAD_REQUEST, "Wrong message format\n")
        return None

    async def _send(
        self, websocket: ServerConnection, message: pb.ServerMessage
    ) -> None:
        if not message.IsInitialized():
            missing = ", ".join(message.FindInitializationErrors())
            raise RuntimeError(f"fake server built an incomplete message: {missing}")
        stored = pb.ServerMessage()
        stored.CopyFrom(message)
        self.sent.append(stored)
        await websocket.send(message.SerializeToString())

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        scenario = self._scenario.on_new_connection()
        self._scenario = scenario
        initial = scenario.initial_message()
        if self.mode is Mode.NEW_RUN_ID:
            initial.state.run_id = "unexpected-fake-run"
        await self._send(websocket, initial)

        if self.mode is Mode.GARBAGE:
            await websocket.send(b"\xff\x00not-a-server-message")
        elif self.mode is Mode.TEXT:
            await websocket.send("not a binary protobuf frame")

        async for frame in websocket:
            if not isinstance(frame, bytes):
                error = build_protocol_error(
                    self.run_id, None, pb.CONTROL_CODE_BAD_MESSAGE, False
                )
                await self._send(websocket, error)
                continue

            message = pb.ClientMessage()
            try:
                message.ParseFromString(frame)
            except Exception:
                error = build_protocol_error(
                    self.run_id, None, pb.CONTROL_CODE_BAD_MESSAGE, False
                )
                await self._send(websocket, error)
                continue
            if not message.IsInitialized() or message.WhichOneof("message") is None:
                error = build_protocol_error(
                    self.run_id, None, pb.CONTROL_CODE_BAD_MESSAGE, False
                )
                await self._send(websocket, error)
                continue

            stored = pb.ClientMessage()
            stored.CopyFrom(message)
            self.received.append(stored)
            if (
                self.mode is Mode.CLOSE_SESSION
                and not self._mode_triggered
                and len(self.received) >= self.trigger_after_messages
            ):
                self._mode_triggered = True
                error = build_protocol_error(
                    self.run_id,
                    None,
                    pb.CONTROL_CODE_SESSION_FENCED,
                    True,
                )
                await self._send(websocket, error)
                await websocket.close(code=1008, reason="protocol error")
                return

            try:
                scenario, replies = scenario.handle(message)
            except ValueError as error:
                if str(error) != "scenario mismatch":
                    raise
                await websocket.close(code=1008, reason="scenario mismatch")
                return
            self._scenario = scenario
            for reply in replies:
                await self._send(websocket, reply)
                if (
                    self.mode is Mode.DROP
                    and not self._mode_triggered
                    and len(self.received) >= self.trigger_after_messages
                ):
                    self._mode_triggered = True
                    websocket.transport.abort()
                    return
                if (
                    reply.WhichOneof("message") == "protocol_error"
                    and reply.protocol_error.close_session
                ):
                    await websocket.close(code=1008, reason="protocol error")
                    return
