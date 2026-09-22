"""
Bytes <-> protobuf messages (contract by package A, body by package D).

This is the only place where bytes from the network become protobuf objects.
Each WebSocket message carries exactly one protobuf message, with no JSON,
Base64 or length prefix around it (the spec's "Connect your client").
"""
from google.protobuf.message import DecodeError

from generated import bazaar_pb2 as pb

from bazaar_client.errors import ProtocolViolation


def encode(message: pb.ClientMessage) -> bytes:
    """The wire bytes for `message` (its SerializeToString()).

    NOT a way to get bytes for sending. encode() runs none of our safety
    checks. Bytes that go to the server must come from guards.check(), which
    checks the message first. encode() is for guards.check() itself, and for
    tests.

    Never changes `message`. If a required field is missing, protobuf's own
    check raises google.protobuf.message.EncodeError.
    """
    # SerializeToString() itself never mutates `message`, and it already
    # raises EncodeError for us if a required field is missing -- there's
    # nothing to add here.
    return message.SerializeToString()


def decode(frame: bytes) -> pb.ServerMessage:
    """A NEW pb.ServerMessage parsed from one binary WebSocket message.

    Raises ProtocolViolation (from bazaar_client.errors) when:
    - `frame` is a str: a text frame, which the spec never sends,
    - the bytes aren't valid protobuf (garbage),
    - a required field is missing, or
    - no message is selected (none of state, result, protocol_error or
      readiness).

    Watch out, verified on 2026-09-21: protobuf 7.36.2's FromString() does NOT
    reject missing required fields. It quietly returns a half-empty message. So
    decode() must call IsInitialized() itself after parsing, and raise if it's
    False.
    """
    # The spec never sends text frames. A str here means the connection layer
    # handed us one anyway, so refuse it before it ever reaches the parser
    # (which would otherwise try to encode it as UTF-8 bytes and confuse a
    # text-frame bug for a protobuf bug).
    if isinstance(frame, str):
        raise ProtocolViolation("received a text frame; the spec only sends binary frames")

    message = pb.ServerMessage()
    try:
        message.ParseFromString(frame)
    except DecodeError as error:
        raise ProtocolViolation(f"could not parse bytes as a ServerMessage: {error}") from error

    # A ServerMessage with none of state/result/protocol_error/readiness set
    # is still "initialized" as far as protobuf is concerned -- a proto2
    # oneof member can't be declared `required`. So this check can't be
    # folded into the IsInitialized() check below; it has to happen on its
    # own.
    if message.WhichOneof("message") is None:
        raise ProtocolViolation(
            "server message selects none of state, result, protocol_error or readiness"
        )

    if not message.IsInitialized():
        missing = ", ".join(message.FindInitializationErrors())
        raise ProtocolViolation(f"server message is missing required fields: {missing}")

    return message
