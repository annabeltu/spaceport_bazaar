"""
Tests for encode/decode: bytes <-> protobuf (package D).

encode() is a thin wrapper around SerializeToString() -- it exists so nothing
outside guards.check() and tests ever calls SerializeToString() directly.
decode() is the more interesting half: protobuf 7.36.2's parser does NOT
check required fields (verified in the plan), so decode() has to call
IsInitialized() itself, and it has to reject a message where no command/
server-message kind was chosen at all, which IsInitialized() alone wouldn't
catch either (oneof members can't be `required` in proto2).
"""
import pytest
from google.protobuf.message import DecodeError, EncodeError

from bazaar_client import codec
from bazaar_client.errors import ProtocolViolation
from factories import make_readiness, make_state
from generated import bazaar_pb2 as pb
from spec import SPEC_MESSAGES, load_spec_message

# --- encode ------------------------------------------------------------------


@pytest.mark.parametrize("spec", SPEC_MESSAGES, ids=lambda spec: spec.filename)
def test_encode_matches_serialize_to_string_for_every_answer_key(spec):
    message = load_spec_message(spec.filename)
    assert codec.encode(message) == message.SerializeToString()


def test_encode_returns_bytes():
    message = load_spec_message("10_sync.textproto")
    assert isinstance(codec.encode(message), bytes)


def test_encode_does_not_change_the_message():
    message = load_spec_message("10_sync.textproto")
    before = message.SerializeToString()
    codec.encode(message)
    assert message.SerializeToString() == before


def test_encode_lets_protobuf_refuse_an_incomplete_message():
    # encode() runs none of our own safety checks -- that's guards.check()'s
    # job. An incomplete message is still rejected, but by protobuf itself.
    incomplete = pb.ClientMessage()
    incomplete.sync.run_id = "test-run-1"  # type and protocol_version missing
    with pytest.raises(EncodeError):
        codec.encode(incomplete)


# --- decode: the happy path ---------------------------------------------------


def test_decode_returns_a_server_message_for_valid_bytes():
    original = make_state()
    decoded = codec.decode(original.SerializeToString())
    assert decoded == original
    assert decoded.WhichOneof("message") == "state"


def test_decode_round_trips_every_field():
    original = make_readiness(ready=True, snapshot_sequence=1)
    decoded = codec.decode(original.SerializeToString())
    assert decoded.readiness.ready is True
    assert decoded.readiness.snapshot_sequence == 1


# --- decode: a text frame -----------------------------------------------------


def test_decode_raises_on_a_str_frame():
    # The spec never sends text frames; a str here means the connection layer
    # got one anyway. The client can't trust it, so decode() refuses it
    # instead of guessing an encoding.
    with pytest.raises(ProtocolViolation):
        codec.decode("not bytes, a str")


# --- decode: garbage bytes -----------------------------------------------------


def test_decode_raises_on_garbage_bytes():
    # An invalid varint tag: protobuf's parser can't make a message of this
    # at all, so it raises DecodeError -- decode() must turn that into our
    # own ProtocolViolation instead of letting it leak out.
    garbage = bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x01])
    with pytest.raises(ProtocolViolation):
        codec.decode(garbage)


def test_garbage_bytes_really_do_fail_to_parse():
    # Sanity check on the fixture above, so the test isn't accidentally
    # passing for the wrong reason (e.g. garbage that parses into junk).
    garbage = bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x01])
    with pytest.raises(DecodeError):
        pb.ServerMessage().ParseFromString(garbage)


# --- decode: missing required fields ------------------------------------------


def test_decode_raises_on_missing_required_fields():
    # SerializeToString() would refuse an incomplete message (that's
    # protobuf's own check), so to get bytes that carry a hole, we bypass it
    # with SerializePartialToString() -- as if an older or buggy server sent
    # a half-built message.
    incomplete = make_state()
    incomplete.state.ClearField("world_version")
    frame = incomplete.SerializePartialToString()
    with pytest.raises(ProtocolViolation):
        codec.decode(frame)


def test_missing_required_field_error_names_the_field():
    # The contract: "name the missing fields in the error", so a beginner
    # debugging a live run can tell what the server left out.
    incomplete = make_state()
    incomplete.state.ClearField("world_version")
    frame = incomplete.SerializePartialToString()
    with pytest.raises(ProtocolViolation, match="world_version"):
        codec.decode(frame)


# --- decode: no message selected (empty oneof) --------------------------------


def test_decode_raises_when_no_message_is_selected():
    # An empty ServerMessage IS "initialized" as far as protobuf is
    # concerned -- oneof members can't be `required` in proto2 -- so this is
    # a separate check decode() has to make on its own.
    empty = pb.ServerMessage()
    assert empty.IsInitialized()  # confirms the gap this test is guarding
    with pytest.raises(ProtocolViolation):
        codec.decode(empty.SerializeToString())


def test_decode_raises_on_completely_empty_bytes():
    # b"" parses to an empty ServerMessage: same gap as above, reached the
    # way it would actually arrive over the wire.
    with pytest.raises(ProtocolViolation):
        codec.decode(b"")
