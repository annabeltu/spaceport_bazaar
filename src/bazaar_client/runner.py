"""
The main read loop (contract and RunOptions by package A, run() by package I).

This is the only module that ties everything together: connection, codec,
state, engine, guards and logs.
"""
import asyncio
from dataclasses import dataclass
from pathlib import Path

from google.protobuf import text_format

from bazaar_client import codec, connection, engine, guards, logs, state
from bazaar_client.credentials import load_credentials
from bazaar_client.errors import (
    ConnectionFailed,
    ConnectionLost,
    CredentialsError,
    GuardError,
    ProtocolViolation,
)


@dataclass(frozen=True)
class RunOptions:
    """What the command line asked for. Built by __main__.parse_args().

    credentials_path: the server's validation-credentials.json.
    url:              where the server listens (connection.DEFAULT_URL unless
                      --url says otherwise).
    dry_run:          connect and read, show the first message we WOULD send,
                      and send nothing.
    log_file:         also write the log here. None means console only.
    """

    credentials_path: Path
    url: str
    dry_run: bool
    log_file: Path | None


async def run(options: RunOptions) -> int:
    """Play the practice script once. Returns the process exit code:
    0 when the engine returns Finish, 1 for anything else.

    The loop, for each message received:
    receive -> codec.decode -> logs.log_received -> state.apply_server_message
    -> engine.decide -> guards.check -> send -> logs.log_sent
    -> record what was sent:
       - a `ready` command: state.record_ready(client,
         message.ready.snapshot_sequence), so the readiness reply is checked
         against the number we declared;
       - a command with a request_id (advertise, offer, accept, withdraw):
         state.record_sent(client, request_id, data);
       - `sync`: nothing to record.

    - It keeps reading even when there's nothing to send (Wait), because the
      server pushes messages on its own.
    - On Stop, GuardError, ProtocolViolation, CredentialsError or
      ConnectionFailed, it sends nothing more, logs why, closes the connection
      and returns 1.
    - On Finish it logs the summary, closes, and returns 0.
    - A protocol_error with close_session: true closes the connection.
    - On ConnectionLost without close_session it reconnects to the same
      server a limited number of times, calls state.on_new_connection(), and
      lets the engine declare readiness again. It reads only from the newest
      connection: the spec says to ignore late messages from an old one.
    - With options.dry_run it prints the first message it would send, in
      readable text, and sends nothing.
    """
    websocket = None
    logger = logs.setup_logging(options.log_file, None)
    try:
        credentials = load_credentials(options.credentials_path, "P01")
        logger = logs.setup_logging(options.log_file, credentials.token)
        client = state.initial()
        position = engine.first_position()
        websocket = await connection.connect(options.url, credentials)
        reconnects = 0

        while True:
            try:
                frame = await websocket.receive()
            except ConnectionLost:
                await websocket.close()
                if reconnects >= 3:
                    raise ConnectionLost("Connection was lost after 3 reconnect attempts.")
                reconnects += 1
                logger.warning("Connection lost; reconnecting (%d/3).", reconnects)
                await asyncio.sleep(0)
                client = state.on_new_connection(client)
                websocket = await connection.connect(options.url, credentials)
                continue

            message = codec.decode(frame)
            logs.log_received(logger, message)
            client = state.apply_server_message(client, message)
            position, decision = engine.decide(client, position, message)

            if isinstance(decision, engine.Wait):
                continue
            if isinstance(decision, engine.Stop):
                logger.error("STOP %s", decision.reason)
                await websocket.close()
                return 1
            if isinstance(decision, engine.Finish):
                logger.info("FINISH %s", decision.summary)
                await websocket.close()
                return 0

            outgoing = decision.message
            if options.dry_run:
                rendered = text_format.MessageToString(
                    outgoing, as_utf8=True, as_one_line=True
                )
                logger.info("DRY RUN would send %s", rendered)
                await websocket.close()
                return 0

            data = guards.check(outgoing, state.guard_context(client))
            await websocket.send(data)
            logs.log_sent(logger, outgoing)
            kind = outgoing.WhichOneof("message")
            command = getattr(outgoing, kind)
            if kind == "ready":
                client = state.record_ready(client, command.snapshot_sequence)
            elif kind != "sync":
                client = state.record_sent(client, command.request_id, data)

    except (
        ConnectionFailed,
        ConnectionLost,
        CredentialsError,
        GuardError,
        ProtocolViolation,
    ) as error:
        logger.error("STOP %s", error)
        if websocket is not None:
            await websocket.close()
        return 1
