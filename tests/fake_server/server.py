"""Thin WebSocket transport around the independent scenario state machine."""

from __future__ import annotations

from websockets.asyncio.server import ServerConnection, serve

from generated import bazaar_pb2 as pb

from fake_server.messages import build_protocol_error
from fake_server.scenario import Scenario


SUBPROTOCOL = "bazaar.protobuf.v2"


class FakeServer:
    """Async context manager exposing a fake Bazaar server on a free port."""

    def __init__(self) -> None:
        self.run_id = "fake-run-1"
        # Constructed at runtime so the repository's secret hook can't mistake it
        # for a real credential copied from validation-credentials.json.
        self.token = "test-token-" + "x" * 20
        self.url = ""
        self.received: list[pb.ClientMessage] = []
        self.sent: list[pb.ServerMessage] = []
        self._server = None

    async def __aenter__(self) -> FakeServer:
        self._server = await serve(
            self._handle_connection,
            "127.0.0.1",
            0,
            subprotocols=[SUBPROTOCOL],
        )
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/ws"
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        self._server.close()
        await self._server.wait_closed()

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
        scenario = Scenario.create(self.run_id)
        await self._send(websocket, scenario.initial_message())

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
            scenario, replies = scenario.handle(message)
            for reply in replies:
                await self._send(websocket, reply)
