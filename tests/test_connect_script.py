"""
Tests for client/connect.py's decode(), the function that turns the
server's bytes into a message.

Why these exist: protobuf 7 does NOT check required fields when parsing
(it only checks when serializing). So without an explicit check, decode()
would hand the rest of the client a message with fields silently missing,
and a zero there looks exactly like a real zero.
"""
import importlib.util
from pathlib import Path

import pytest

from generated import bazaar_pb2 as pb

REPO_ROOT = Path(__file__).resolve().parent.parent
CONNECT_SCRIPT = REPO_ROOT / "client" / "connect.py"


def load_connect_script():
    """Import client/connect.py from its file path.

    client/ isn't on the import path (pyproject.toml only adds src/ and
    tests/), so we load the file directly instead of changing that config.
    """
    spec = importlib.util.spec_from_file_location("connect_script", CONNECT_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


connect_script = load_connect_script()


def complete_readiness_message():
    """A small ServerMessage with every required field set."""
    return pb.ServerMessage(
        readiness=pb.Readiness(
            type=pb.READINESS_TYPE_READINESS,
            protocol_version="2.0",
            run_id="test-run-1",
            ready=True,
            snapshot_sequence=1,
        )
    )


def test_decode_returns_a_complete_message_unchanged():
    original = complete_readiness_message()
    decoded = connect_script.decode(original.SerializeToString())
    assert decoded == original


def test_protobuf_parsing_alone_does_not_catch_missing_fields():
    # Documents the gap decode() has to close. If a protobuf upgrade ever
    # starts checking on its own, this fails: a prompt to revisit, not a bug.
    incomplete = complete_readiness_message()
    incomplete.readiness.ClearField("snapshot_sequence")
    parsed = pb.ServerMessage.FromString(incomplete.SerializePartialToString())
    assert not parsed.IsInitialized()


def test_decode_rejects_a_message_with_missing_required_fields():
    incomplete = complete_readiness_message()
    incomplete.readiness.ClearField("snapshot_sequence")
    with pytest.raises(RuntimeError, match="readiness.snapshot_sequence"):
        connect_script.decode(incomplete.SerializePartialToString())


def test_decode_rejects_a_message_with_nothing_selected():
    # Zero bytes parse as a ServerMessage with no state/result/etc. chosen.
    with pytest.raises(RuntimeError, match="no message"):
        connect_script.decode(b"")


def test_decode_rejects_text():
    with pytest.raises(RuntimeError, match="text"):
        connect_script.decode("not bytes")
