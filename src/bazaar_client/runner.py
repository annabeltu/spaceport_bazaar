"""
The main read loop (contract and RunOptions by package A, run() by package I).

This is the only module that ties everything together: connection, codec,
state, engine, guards and logs.
"""
from dataclasses import dataclass
from pathlib import Path


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
    -> state.record_sent.

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
    raise NotImplementedError("package I")
