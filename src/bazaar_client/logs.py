"""
Logging, with the token hidden (contract by package A, body by package I).

Every message in and out is logged as readable text with a timestamp, to the
console and, if asked, to a file. That's how we check what the client
actually decoded; the spec warns that the server's report can't check that.

The token should never reach a log in the first place. The redaction here is
a backstop in case something ever tries to log it.
"""
import logging
from pathlib import Path

from generated import bazaar_pb2 as pb

# The name of the client's logger: logging.getLogger(LOGGER_NAME).
LOGGER_NAME = "bazaar_client"

# What the token, or anything after "Bearer ", is replaced with.
REDACTED = "[REDACTED]"

# Each logged message starts with one of these words. Packages J and K count
# these lines to check the spec's "8 messages sent and 16 received".
SENT_LABEL = "SENT"
RECEIVED_LABEL = "RECEIVED"


def redact(text: str, token: str | None) -> str:
    """`text` with the token, and anything after "Bearer ", replaced by REDACTED.

    A token of None or "" means "no token known yet", and then only the
    "Bearer " rule applies. (Careful: never call text.replace("", ...). An
    empty string matches between every pair of characters.)
    """
    raise NotImplementedError("package I")


def setup_logging(log_file: Path | None, token: str | None) -> logging.Logger:
    """The client's logger, ready to use.

    Writes timestamped lines to the console, and to `log_file` too unless it's
    None. Every handler gets a filter that runs redact() on each line. Put the
    filter on the handlers, not the logger: a logger's own filters skip lines
    passed up from other loggers, such as the `websockets` library's.

    Calling it again replaces the handlers from the earlier call, so tests
    that call it many times don't get every line twice.
    """
    raise NotImplementedError("package I")


def log_sent(logger: logging.Logger, message: pb.ClientMessage) -> None:
    """Log one line for a message we sent: SENT_LABEL, then the message in
    one-line protobuf text form."""
    raise NotImplementedError("package I")


def log_received(logger: logging.Logger, message: pb.ServerMessage) -> None:
    """Log one line for a message we received: RECEIVED_LABEL, then the
    message in one-line protobuf text form."""
    raise NotImplementedError("package I")
