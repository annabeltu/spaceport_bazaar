"""
Bytes <-> protobuf messages (contract by package A, body by package D).

This is the only place where bytes from the network become protobuf objects.
Each WebSocket message carries exactly one protobuf message, with no JSON,
Base64 or length prefix around it (the spec's "Connect your client").
"""
from generated import bazaar_pb2 as pb


def encode(message: pb.ClientMessage) -> bytes:
    """The wire bytes for `message` (its SerializeToString()).

    NOT a way to get bytes for sending. encode() runs none of our safety
    checks. Bytes that go to the server must come from guards.check(), which
    checks the message first. encode() is for guards.check() itself, and for
    tests.

    Never changes `message`. If a required field is missing, protobuf's own
    check raises google.protobuf.message.EncodeError.
    """
    raise NotImplementedError("package D")


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
    raise NotImplementedError("package D")
