"""Small safety checks around Annabel's existing send and credential helpers."""
import asyncio
import json
import traceback
from unittest.mock import AsyncMock

import pytest

from client import connect as client
from client.connection import decode, send
from client.messages import build_offer, build_ready
from generated import bazaar_pb2 as pb


@pytest.mark.parametrize('problem', [
    'empty', 'missing field', 'version', 'empty run', 'placeholder run',
    'empty request', 'invalid request', 'long request', 'oversized',
])
def test_bad_command_never_reaches_socket(problem):
    message = build_offer('test-run')
    if problem == 'empty':
        message.Clear()
    elif problem == 'missing field':
        message.offer.body.give.ClearField('food')
    elif problem == 'version':
        message.offer.protocol_version = 'wrong'
    elif problem == 'empty run':
        message.offer.run_id = ''
    elif problem == 'placeholder run':
        message.offer.run_id = '<RUN_ID>'
    elif problem == 'empty request':
        message.offer.request_id = ''
    elif problem == 'invalid request':
        message.offer.request_id = 'request with spaces'
    elif problem == 'long request':
        message.offer.request_id = 'x' * 65
    else:
        message.offer.body.recipient_id = 'x' * 16_384
    websocket = AsyncMock()

    with pytest.raises(RuntimeError):
        asyncio.run(send(websocket, message))

    websocket.send.assert_not_called()


@pytest.mark.parametrize('message', [build_ready('test-run', 1), build_offer('test-run')])
def test_valid_command_sent_once_as_unchanged_bytes(message):
    websocket = AsyncMock()
    asyncio.run(send(websocket, message))
    websocket.send.assert_awaited_once_with(message.SerializeToString())


def test_decode_rejects_other_protocol_version():
    message = pb.ServerMessage()
    message.readiness.CopyFrom(pb.Readiness(
        type=pb.READINESS_TYPE_READINESS, protocol_version='wrong',
        run_id='test-run', ready=True, snapshot_sequence=1))
    with pytest.raises(RuntimeError, match='protocol version'):
        decode(message.SerializeToString())


@pytest.mark.parametrize('contents', [
    '{"fake-sensitive":', '[]', '{"players": null}',
    '{"players": [null]}',
    '{"players": [{"station_id": "P01", "token": 123}]}',
])
def test_bad_credentials_give_safe_error(tmp_path, monkeypatch, contents):
    path = tmp_path / 'credentials.json'
    path.write_text(contents)
    monkeypatch.setattr(client, 'CREDENTIALS', path)
    with pytest.raises(SystemExit) as caught:
        client.p01_token()
    assert 'credentials' in str(caught.value).lower()
    assert 'fake-sensitive' not in ''.join(traceback.format_exception(caught.value))


def test_credentials_are_read_fresh_and_never_printed(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'credentials.json'
    monkeypatch.setattr(client, 'CREDENTIALS', path)
    for token in ('fake-first', 'fake-second'):
        path.write_text(json.dumps({'players': [
            {'station_id': 'P02', 'token': 'fake-other'},
            {'station_id': 'P01', 'token': token},
        ]}))
        assert client.p01_token() == token
    captured = capsys.readouterr()
    assert captured.out == captured.err == ''


def test_missing_credentials_explain_how_to_start(tmp_path, monkeypatch):
    monkeypatch.setattr(client, 'CREDENTIALS', tmp_path / 'missing.json')
    with pytest.raises(SystemExit, match='Start the server first'):
        client.p01_token()
