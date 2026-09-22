"""Replay a token-free transcript captured from the real practice server."""

from pathlib import Path

from google.protobuf import text_format

from bazaar_client import engine, guards, state
from generated import bazaar_pb2 as pb


RECORDING = Path(__file__).parent / "fixtures" / "recorded" / "practice-run.log"


def _payload(line, label):
    marker = f" INFO {label} "
    return line.split(marker, 1)[1] if marker in line else None


def test_real_server_recording_replays_through_state_engine_and_guards():
    client = state.initial()
    position = engine.first_position()
    expected_send = None
    sent = received = 0
    finished = False

    for line in RECORDING.read_text().splitlines():
        incoming_text = _payload(line, "RECEIVED")
        outgoing_text = _payload(line, "SENT")
        if incoming_text is not None:
            message = text_format.Parse(incoming_text, pb.ServerMessage())
            client = state.apply_server_message(client, message)
            position, decision = engine.decide(client, position, message)
            received += 1
            if isinstance(decision, engine.Send):
                expected_send = decision.message
            elif isinstance(decision, engine.Finish):
                finished = True
            else:
                assert not isinstance(decision, engine.Stop), decision.reason
        elif outgoing_text is not None:
            actual = text_format.Parse(outgoing_text, pb.ClientMessage())
            assert expected_send is not None
            assert actual.SerializeToString() == expected_send.SerializeToString()
            data = guards.check(actual, state.guard_context(client))
            kind = actual.WhichOneof("message")
            command = getattr(actual, kind)
            if kind == "ready":
                client = state.record_ready(client, command.snapshot_sequence)
            elif kind != "sync":
                client = state.record_sent(client, command.request_id, data)
            expected_send = None
            sent += 1

    assert finished
    assert expected_send is None
    assert sent == 8
    assert received == 16
    assert client.snapshot.self.inventory.water == 28
    assert client.snapshot.self.inventory.food == 31
    assert client.snapshot.self.inventory.components == 31
