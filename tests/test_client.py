import pytest
from google.protobuf.message import DecodeError
from generated import bazaar_pb2 as pb
from client.connection import decode
from client.messages import build_ready, build_advertisement, build_offer
from client.state import validate_initial, validate_offer, validate_result


def roundtrip(message):
    return pb.ClientMessage.FromString(message.SerializeToString())


def test_ready():
    ready = roundtrip(build_ready('test-run', 1)).ready
    assert ready.type == pb.READY_TYPE_READY
    assert ready.protocol_version == '2.0'
    assert ready.run_id == 'test-run'
    assert ready.ready and ready.snapshot_sequence == 1


@pytest.mark.parametrize('selling,seeking', [
    ([pb.RESOURCE_WATER], [pb.RESOURCE_FOOD]),
    ([], [pb.RESOURCE_COMPONENTS]),
    ([], []),
])
def test_advertisement_preserves_required_containers(selling, seeking):
    ad = roundtrip(build_advertisement('test-run', 'request-1', selling, seeking)).advertise
    assert ad.type == pb.ADVERTISE_TYPE_ADVERTISE
    assert (ad.protocol_version, ad.run_id, ad.request_id) == ('2.0', 'test-run', 'request-1')
    assert ad.body.HasField('selling') and ad.body.HasField('seeking')
    assert list(ad.body.selling.items) == selling
    assert list(ad.body.seeking.items) == seeking
    assert ad.body.expires_tick == 6


def test_offer_includes_zero_quantities():
    offer = roundtrip(build_offer('test-run')).offer
    assert offer.type == pb.OFFER_COMMAND_TYPE_OFFER
    assert (offer.protocol_version, offer.run_id, offer.request_id) == ('2.0', 'test-run', 'student-offer-1')
    assert offer.body.recipient_id == 'P02'
    assert offer.body.give == pb.Bundle(water=2, food=0, components=0)
    assert offer.body.receive == pb.Bundle(water=0, food=1, components=0)
    assert offer.body.expires_tick == 6


def test_decode_binary_readiness():
    message = pb.ServerMessage()
    message.readiness.CopyFrom(pb.Readiness(
        type=pb.READINESS_TYPE_READINESS, protocol_version='2.0',
        run_id='test-run', ready=True, snapshot_sequence=1))
    assert decode(message.SerializeToString()) == message


@pytest.mark.parametrize('payload', ['text', b'', b'\x22\x00'])
def test_decode_rejects_text_empty_and_incomplete(payload):
    with pytest.raises(RuntimeError):
        decode(payload)


def test_decode_rejects_malformed_bytes():
    with pytest.raises(DecodeError):
        decode(b'\xff')


@pytest.fixture
def initial():
    state = pb.State(self_station_id='P01', world_version=2, snapshot_sequence=1)
    station = getattr(state, 'self')
    station.inventory.CopyFrom(pb.Bundle(water=30, food=30, components=30))
    station.specialty = pb.RESOURCE_WATER
    ad = state.advertisements.items.add(station_id='P02', status=pb.PUBLICATION_STATUS_ACTIVE)
    ad.selling.items.append(pb.RESOURCE_FOOD)
    ad.seeking.items.append(pb.RESOURCE_WATER)
    return state


def test_initial_and_supported_reconnects(initial):
    for version in (2, 3, 4):
        initial.world_version = version
        validate_initial(initial)


@pytest.mark.parametrize('field,value', [
    ('self_station_id', 'P02'), ('world_version', 1), ('world_version', 5),
    ('snapshot_sequence', 2),
])
def test_initial_rejects_wrong_identity_or_counters(initial, field, value):
    setattr(initial, field, value)
    with pytest.raises(RuntimeError):
        validate_initial(initial)


@pytest.mark.parametrize('resource', ['water', 'food', 'components'])
def test_initial_rejects_wrong_inventory(initial, resource):
    setattr(getattr(initial, 'self').inventory, resource, 29)
    with pytest.raises(RuntimeError):
        validate_initial(initial)


def test_initial_rejects_wrong_specialty(initial):
    getattr(initial, 'self').specialty = pb.RESOURCE_FOOD
    with pytest.raises(RuntimeError):
        validate_initial(initial)


@pytest.mark.parametrize('change', ['missing', 'seller', 'selling', 'seeking', 'inactive'])
def test_initial_rejects_wrong_peer_advertisement(initial, change):
    ad = initial.advertisements.items[0]
    if change == 'missing':
        initial.advertisements.ClearField('items')
    elif change == 'seller':
        ad.station_id = 'P01'
    elif change == 'inactive':
        ad.status = pb.PUBLICATION_STATUS_EXPIRED
    else:
        getattr(ad, change).items[:] = [pb.RESOURCE_COMPONENTS]
    with pytest.raises(RuntimeError):
        validate_initial(initial)


def test_offer_validation_rejects_wrong_amount(initial):
    offer = initial.offers.items.add(offer_id='offer-1', proposer_id='P01',
        recipient_id='P02', expires_tick=6, status=pb.OFFER_STATUS_OPEN)
    offer.give.CopyFrom(pb.Bundle(water=2, food=0, components=0))
    offer.receive.CopyFrom(pb.Bundle(water=0, food=1, components=0))
    validate_offer(initial, 'offer-1')
    offer.give.water = 3
    with pytest.raises(RuntimeError):
        validate_offer(initial, 'offer-1')


def test_result_must_match_request():
    result = pb.Result(run_id='run', request_id='request', ok=True, code=pb.RESULT_CODE_OK)
    result.object_id.value = 'advertisement-4'
    assert validate_result(result, 'run', 'request') == 'advertisement-4'
    with pytest.raises(RuntimeError):
        validate_result(result, 'run', 'other-request')


@pytest.fixture
def accepted(initial):
    initial.world_version = 6
    initial.snapshot_sequence = 5
    getattr(initial, 'self').inventory.CopyFrom(pb.Bundle(water=28, food=31, components=30))
    initial.offers.items.add(offer_id='offer-1', status=pb.OFFER_STATUS_ACCEPTED)
    initial.transactions.items.add(offer_id='offer-1')
    return initial


def test_acceptance(accepted):
    from client.state import validate_acceptance, validate_progress
    validate_progress(accepted, 6, 5, (28, 31, 30))
    validate_acceptance(accepted, 'offer-1')


@pytest.mark.parametrize('change', ['missing_offer', 'open', 'missing_transaction',
                                    'extra_transaction', 'wrong_transaction', 'inventory'])
def test_acceptance_rejects_invalid_state(accepted, change):
    from client.state import validate_acceptance
    if change == 'missing_offer':
        accepted.offers.ClearField('items')
    elif change == 'open':
        accepted.offers.items[0].status = pb.OFFER_STATUS_OPEN
    elif change == 'missing_transaction':
        accepted.transactions.ClearField('items')
    elif change == 'extra_transaction':
        accepted.transactions.items.add(offer_id='offer-1')
    elif change == 'wrong_transaction':
        accepted.transactions.items[0].offer_id = 'other'
    else:
        getattr(accepted, 'self').inventory.water = 30
    with pytest.raises(RuntimeError):
        validate_acceptance(accepted, 'offer-1')


@pytest.fixture
def gift(accepted):
    accepted.world_version = 7
    accepted.snapshot_sequence = 6
    offer = accepted.offers.items.add(offer_id='gift-1', proposer_id='P02',
                                     recipient_id='P01', status=pb.OFFER_STATUS_OPEN)
    offer.give.CopyFrom(pb.Bundle(water=0, food=0, components=1))
    offer.receive.CopyFrom(pb.Bundle(water=0, food=0, components=0))
    return accepted


def test_gift(gift):
    from client.state import validate_acceptance, validate_gift, validate_progress
    validate_progress(gift, 7, 6, (28, 31, 30))
    validate_acceptance(gift, 'offer-1')
    assert validate_gift(gift) == 'gift-1'


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'proposer', 'recipient',
                                    'closed', 'quantity', 'price', 'id', 'inventory'])
def test_gift_rejects_invalid_state(gift, change):
    from client.state import validate_gift
    offer = gift.offers.items[1]
    if change == 'missing':
        del gift.offers.items[1]
    elif change == 'duplicate':
        gift.offers.items.add().CopyFrom(offer)
    elif change == 'proposer':
        offer.proposer_id = 'P01'
    elif change == 'recipient':
        offer.recipient_id = 'P02'
    elif change == 'closed':
        offer.status = pb.OFFER_STATUS_ACCEPTED
    elif change == 'quantity':
        offer.give.components = 2
    elif change == 'price':
        offer.receive.water = 1
    elif change == 'id':
        offer.offer_id = ''
    else:
        getattr(gift, 'self').inventory.components = 31
    with pytest.raises(RuntimeError):
        validate_gift(gift)


@pytest.mark.parametrize('version,sequence', [(5, 5), (6, 4), (7, 5)])
def test_automatic_update_rejects_wrong_counters(accepted, version, sequence):
    from client.state import validate_progress
    accepted.world_version = version
    accepted.snapshot_sequence = sequence
    with pytest.raises(RuntimeError):
        validate_progress(accepted, 6, 5, (28, 31, 30))


def test_accept_serialization():
    from client.messages import build_accept
    command = roundtrip(build_accept('test-run', 'gift-1')).accept
    assert command.type == pb.ACCEPT_TYPE_ACCEPT
    assert (command.protocol_version, command.run_id, command.request_id) == (
        '2.0', 'test-run', 'student-accept-1')
    assert command.body.offer_id == 'gift-1'


@pytest.fixture
def acceptance_result():
    result = pb.Result(run_id='test-run', request_id='student-accept-1',
                       ok=True, code=pb.RESULT_CODE_OK)
    result.object_id.value = 'gift-1'
    result.transaction_id.value = 'tx-2'
    return result


def test_accept_result(acceptance_result):
    from client.state import validate_accept_result
    assert validate_accept_result(
        acceptance_result, 'test-run', 'student-accept-1', 'gift-1') == 'tx-2'


@pytest.mark.parametrize('change', ['offer', 'transaction', 'run', 'request', 'failure'])
def test_accept_result_rejects_mismatch(acceptance_result, change):
    from client.state import validate_accept_result
    if change == 'offer':
        acceptance_result.object_id.value = 'other'
    elif change == 'transaction':
        acceptance_result.transaction_id.ClearField('value')
    elif change == 'run':
        acceptance_result.run_id = 'other'
    elif change == 'request':
        acceptance_result.request_id = 'other'
    else:
        acceptance_result.ok = False
    with pytest.raises(RuntimeError):
        validate_accept_result(acceptance_result, 'test-run', 'student-accept-1', 'gift-1')


@pytest.fixture
def settled_gift(gift):
    gift.world_version = 8
    gift.snapshot_sequence = 7
    getattr(gift, 'self').inventory.components = 31
    gift.offers.items[1].status = pb.OFFER_STATUS_ACCEPTED
    gift.offers.items[1].transaction_id.value = 'tx-2'
    tx = gift.transactions.items.add(transaction_id='tx-2', offer_id='gift-1',
                                    proposer_id='P02', recipient_id='P01')
    tx.give.CopyFrom(pb.Bundle(water=0, food=0, components=1))
    tx.receive.CopyFrom(pb.Bundle(water=0, food=0, components=0))
    ad = gift.advertisements.items.add(advertisement_id='ad-1', station_id='P01',
                                      status=pb.PUBLICATION_STATUS_ACTIVE, expires_tick=6)
    ad.seeking.items.append(pb.RESOURCE_COMPONENTS)
    return gift


def test_gift_settlement(settled_gift):
    from client.state import validate_gift_accepted, validate_progress
    validate_progress(settled_gift, 8, 7, (28, 31, 31))
    validate_gift_accepted(settled_gift, 'gift-1', 'tx-2', 'ad-1')


@pytest.mark.parametrize('change', ['inventory', 'open', 'offer_transaction', 'count',
                                    'transaction_id', 'transaction_offer', 'price',
                                    'advertisement_missing', 'advertisement_expired'])
def test_gift_settlement_rejects_invalid_state(settled_gift, change):
    from client.state import validate_gift_accepted
    state = settled_gift
    if change == 'inventory':
        getattr(state, 'self').inventory.components = 30
    elif change == 'open':
        state.offers.items[1].status = pb.OFFER_STATUS_OPEN
    elif change == 'offer_transaction':
        state.offers.items[1].transaction_id.value = 'other'
    elif change == 'count':
        state.transactions.items.add(transaction_id='extra')
    elif change == 'transaction_id':
        state.transactions.items[1].transaction_id = 'other'
    elif change == 'transaction_offer':
        state.transactions.items[1].offer_id = 'other'
    elif change == 'price':
        state.transactions.items[1].receive.water = 1
    elif change == 'advertisement_missing':
        del state.advertisements.items[1]
    else:
        state.advertisements.items[1].status = pb.PUBLICATION_STATUS_EXPIRED
    with pytest.raises(RuntimeError):
        validate_gift_accepted(state, 'gift-1', 'tx-2', 'ad-1')


@pytest.mark.parametrize('version', [5, 6, 7, 8])
def test_reconnect_resumes_without_replaying_commands(
        settled_gift, monkeypatch, version):
    import asyncio
    from copy import deepcopy
    from types import SimpleNamespace
    import client.connect as client

    # Build snapshots of each stage from the completed exercise.
    completed = settled_gift
    completed.run_id = 'test-run'
    original = completed.offers.items[0]
    original.proposer_id = 'P01'
    original.recipient_id = 'P02'
    original.expires_tick = 6
    original.give.CopyFrom(pb.Bundle(water=2, food=0, components=0))
    original.receive.CopyFrom(pb.Bundle(water=0, food=1, components=0))
    snapshots = {}
    for stage in range(version, 9):
        state = deepcopy(completed)
        state.world_version = stage
        state.snapshot_sequence = stage - version + 1
        if stage < 8:
            del state.transactions.items[1:]
            getattr(state, 'self').inventory.components = 30
            state.offers.items[1].status = pb.OFFER_STATUS_OPEN
            state.offers.items[1].ClearField('transaction_id')
        if stage < 7:
            del state.offers.items[1:]
        if stage == 5:
            state.offers.items[0].status = pb.OFFER_STATUS_OPEN
            state.transactions.ClearField('items')
            getattr(state, 'self').inventory.CopyFrom(
                pb.Bundle(water=30, food=30, components=30))
        snapshots[stage] = state

    replies = [('state', snapshots[version]),
               ('readiness', pb.Readiness(ready=True, run_id='test-run', snapshot_sequence=1))]
    for stage in range(version + 1, 8):
        replies.append(('state', snapshots[stage]))
    if version < 8:
        result = pb.Result(run_id='test-run', request_id='student-accept-1',
                           ok=True, code=pb.RESULT_CODE_OK)
        result.object_id.value = 'gift-1'
        result.transaction_id.value = 'tx-2'
        replies.extend([('result', result), ('state', snapshots[8])])
    sent = []

    class Connection:
        subprotocol = client.SUBPROTOCOL

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    async def receive(websocket, expected=None):
        kind, payload = replies.pop(0)
        assert kind == expected
        return SimpleNamespace(**{kind: payload})

    async def send(websocket, message):
        sent.append(message)

    monkeypatch.setattr(client, 'connect', lambda *args, **kwargs: Connection())
    monkeypatch.setattr(client, 'p01_token', lambda: 'test-token')
    monkeypatch.setattr(client, 'receive', receive)
    monkeypatch.setattr(client, 'send', send)
    asyncio.run(client.main())
    assert not replies
    assert [message.WhichOneof('message') for message in sent] == (
        ['ready'] if version == 8 else ['ready', 'accept'])
    if version < 8:
        assert sent[1].accept.body.offer_id == 'gift-1'
