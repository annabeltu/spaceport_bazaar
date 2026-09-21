"""
Shared exceptions (package A). Frozen once package A merges.

Each class is a different KIND of failure, so the runner can react to each one
differently: stop, reconnect, or tell the user to restart the server. None of
them inherits from another, so `except GuardError` never catches anything else
by accident.

The rule for every one of them: the error message never contains the token.
"""


class GuardError(Exception):
    """A message failed a pre-send check in guards.check(). Nothing was sent.

    The message names the check that failed, e.g. "request_id: must be 1-64
    letters, digits, '_' or '-'". The runner stops instead of sending.
    """


class ProtocolViolation(Exception):
    """The server sent something the spec rules out.

    For example: a text frame instead of binary, bytes that aren't a valid
    ServerMessage, a message with a required field missing, or one with no
    message selected. The client can't trust anything after that, so it stops.
    """


class CredentialsError(Exception):
    """P01's token can't be read from the server's credentials file.

    For example: the file doesn't exist (start the server first), no entry has
    the station ID we asked for, or that entry has no token. The message never
    contains the token.
    """


class ConnectionFailed(Exception):
    """connection.connect() couldn't open a session with the server.

    Raised for HTTP 401 (the server rejected the token), HTTP 400 (wrong
    station or subprotocol), a server that didn't confirm the
    bazaar.protobuf.v2 subprotocol, or nothing listening at the URL. The
    message never contains the token.
    """


class ConnectionLost(Exception):
    """A connection that was open has closed or dropped.

    Raised by Connection.send() and Connection.receive(). If the server didn't
    ask us to close (close_session), the runner may reconnect.
    """
