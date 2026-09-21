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
