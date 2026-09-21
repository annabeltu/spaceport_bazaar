"""Thin WebSocket transport around the independent scenario state machine."""

from __future__ import annotations

import asyncio
from enum import Enum
from http import HTTPStatus
import json
from pathlib import Path

from websockets.http11 import Request, Response
from websockets.asyncio.server import ServerConnection, serve
from google.protobuf.message import DecodeError

from generated import bazaar_pb2 as pb

from fake_server.messages import build_protocol_error
from fake_server.scenario import Scenario
from fake_server.wire import InvalidWire, validate_wire


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
        self._active_connection: ServerConnection | None = None
        self._connection_lock: asyncio.Lock | None = None
        self._scenario_lock: asyncio.Lock | None = None

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
        self._connection_lock = asyncio.Lock()
        self._scenario_lock = asyncio.Lock()
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
        initial = await self._begin_connection(websocket)
        if self.mode is Mode.NEW_RUN_ID:
            initial.state.run_id = "unexpected-fake-run"
        await self._send(websocket, initial)

        if self.mode is Mode.GARBAGE:
            await websocket.send(b"\xff\x00not-a-server-message")
        elif self.mode is Mode.TEXT:
            await websocket.send("not a binary protobuf frame")

        try:
            async for frame in websocket:
                if not isinstance(frame, bytes) or len(frame) > 16_384:
                    await self._send_bad_message(websocket)
                    continue
                message = self._parse(frame)
                if message is None:
                    await self._send_bad_message(websocket)
                    continue
                if await self._handle_mode_before_command(websocket, message):
                    return
                replies = await self._apply(message, websocket)
                if replies is None:
                    return
                if await self._send_replies(websocket, replies):
                    return
        finally:
            async with self._connection_lock:
                if self._active_connection is websocket:
                    self._active_connection = None

    async def _begin_connection(self, websocket: ServerConnection) -> pb.ServerMessage:
        async with self._connection_lock:
            async with self._scenario_lock:
                previous = self._active_connection
                if previous is not None and previous is not websocket:
                    # UNVERIFIED: the spec doesn't name the old connection's exact
                    # response. Package K checks SESSION_FENCED against a recording.
                    fenced = build_protocol_error(
                        self.run_id, None, pb.CONTROL_CODE_SESSION_FENCED, True
                    )
                    await self._send(previous, fenced)
                    await previous.close(code=1008, reason="session fenced")
                self._active_connection = websocket
                self._scenario = self._scenario.on_new_connection()
                return self._scenario.initial_message()

    def _parse(self, frame: bytes) -> pb.ClientMessage | None:
        message = pb.ClientMessage()
        try:
            validate_wire(frame, pb.ClientMessage.DESCRIPTOR)
            message.ParseFromString(frame)
        except (DecodeError, InvalidWire):
            return None
        if not message.IsInitialized() or message.WhichOneof("message") is None:
            return None
        return message

    async def _send_bad_message(self, websocket: ServerConnection) -> None:
        error = build_protocol_error(
            self.run_id, None, pb.CONTROL_CODE_BAD_MESSAGE, False
        )
        await self._send(websocket, error)

    async def _handle_mode_before_command(
        self, websocket: ServerConnection, message: pb.ClientMessage
    ) -> bool:
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
                self.run_id, None, pb.CONTROL_CODE_SESSION_FENCED, True
            )
            await self._send(websocket, error)
            await websocket.close(code=1008, reason="protocol error")
            return True
        return False

    async def _apply(
        self, message: pb.ClientMessage, websocket: ServerConnection
    ) -> tuple[pb.ServerMessage, ...] | None:
        async with self._scenario_lock:
            try:
                scenario, replies = self._scenario.handle(message)
            except ValueError as error:
                if str(error) != "scenario mismatch":
                    raise
                # UNVERIFIED: the spec says the report ends the run, but doesn't
                # state a close code or reason. Package K checks the real behavior.
                await websocket.close(code=1008, reason="scenario mismatch")
                return None
            self._scenario = scenario
            return replies

    async def _send_replies(
        self, websocket: ServerConnection, replies: tuple[pb.ServerMessage, ...]
    ) -> bool:
        for reply in replies:
            await self._send(websocket, reply)
            if (
                self.mode is Mode.DROP
                and not self._mode_triggered
                and len(self.received) >= self.trigger_after_messages
            ):
                self._mode_triggered = True
                websocket.transport.abort()
                return True
            if (
                reply.WhichOneof("message") == "protocol_error"
                and reply.protocol_error.close_session
            ):
                await websocket.close(code=1008, reason="protocol error")
                return True
        return False
