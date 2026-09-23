import pytest

from generated import bazaar_pb2 as pb
from client.trading import Trader, RESOURCES, describe


@pytest.fixture
def state():
    state = pb.State(run_id='live', self_station_id='P06', tick=20, phase=pb.PHASE_RUNNING)
    station = getattr(state, 'self')
    station.specialty = pb.RESOURCE_COMPONENTS
    station.health = 80
    station.inventory.CopyFrom(pb.Bundle(water=0, food=0, components=50))
    station.upkeep_per_tick.CopyFrom(pb.Bundle(water=1, food=1, components=1))
    state.rules.CopyFrom(pb.PublicRules(duration_ticks=120, new_commands_per_station_per_tick=10,
        max_request_records_per_station=2048, max_open_outgoing_offers=24,
        max_offer_ttl_ticks=12, max_publication_ttl_ticks=12, max_command_bytes=16384))
    for peer in ('P01', 'P02', 'P06'):
        state.directory.items.add(station_id=peer)
    return state


def incoming(state, **cost):
    offer = state.offers.items.add(offer_id='incoming', proposer_id='P01', recipient_id='P06',
                                  expires_tick=25, status=pb.OFFER_STATUS_OPEN)
    offer.give.CopyFrom(pb.Bundle(water=3, food=0, components=0))
    offer.receive.CopyFrom(pb.Bundle(**{r: cost.get(r, 0) for r in RESOURCES}))
    return offer


@pytest.mark.parametrize('specialty', [1, 2, 3])
def test_specialty_is_recovered_from_snapshot(state, specialty):
    station = getattr(state, 'self')
    station.specialty = specialty
    station.inventory.CopyFrom(pb.Bundle(**{r: 50 if i + 1 == specialty else 0
                                           for i, r in enumerate(RESOURCES)}))
    commands = Trader().plan(state)
    assert len(commands) == 3
    assert list(commands[0].advertise.body.selling.items) == [specialty]
    assert RESOURCES[specialty - 1] in describe(state)
    for command in commands:
        command.SerializeToString()  # All proto2 required fields, including zeros.
    for command in commands[1:]:
        assert getattr(command.offer.body.give, RESOURCES[specialty - 1]) == 3
        assert command.offer.body.recipient_id != 'P06'


def test_accepts_components_for_water_in_correct_direction(state):
    incoming(state, components=3)
    commands = Trader().plan(state)
    assert commands[0].accept.body.offer_id == 'incoming'


@pytest.mark.parametrize('change', ['expired', 'closed', 'unaffordable', 'scarce', 'overpriced'])
def test_rejects_unusable_or_harmful_offer(state, change):
    offer = incoming(state, components=3)
    if change == 'expired':
        offer.expires_tick = state.tick
    elif change == 'closed':
        offer.status = pb.OFFER_STATUS_WITHDRAWN
    elif change == 'unaffordable':
        getattr(state, 'self').inventory.components = 3
    elif change == 'scarce':
        offer.receive.food = 1
    else:
        offer.receive.components = 4
    assert all(c.WhichOneof('message') != 'accept' for c in Trader().plan(state))


@pytest.mark.parametrize('change', ['paused', 'finished', 'failed', 'zero_health'])
def test_does_not_trade_when_unavailable(state, change):
    if change == 'paused':
        state.phase = pb.PHASE_PAUSED
    elif change == 'finished':
        state.phase = pb.PHASE_FINISHED
    elif change == 'failed':
        getattr(state, 'self').failed_once = True
    else:
        getattr(state, 'self').health = 0
    assert Trader().plan(state) == []


def test_tick_budget_and_reconnect_existing_offers(state):
    trader = Trader()
    commands = trader.plan(state)
    assert trader.plan(state) == []
    for message in commands[1:]:
        body = message.offer.body
        state.offers.items.add(proposer_id='P06', recipient_id=body.recipient_id,
            give=body.give, receive=body.receive, expires_tick=body.expires_tick,
            status=pb.OFFER_STATUS_OPEN)
    # All pending promises are reserved locally, preserving upkeep.
    getattr(state, 'self').inventory.components = 8
    assert all(c.WhichOneof('message') != 'offer' for c in Trader().plan(state))
    state.rules.new_commands_per_station_per_tick = 1
    assert len(Trader().plan(state)) == 1
    state.request_results.items.add(processed_tick=state.tick)
    assert Trader().plan(state) == []


def test_known_seller_is_preferred(state):
    ad = state.advertisements.items.add(station_id='P02',
        status=pb.PUBLICATION_STATUS_ACTIVE, expires_tick=25)
    ad.selling.items.append(pb.RESOURCE_WATER)
    ad.seeking.items.append(pb.RESOURCE_COMPONENTS)
    offers = [c.offer for c in Trader().plan(state) if c.WhichOneof('message') == 'offer']
    assert next(o for o in offers if o.body.receive.water).body.recipient_id == 'P02'


def test_live_trades_latest_state_after_readiness(state, monkeypatch):
    import asyncio
    from copy import deepcopy
    import client.live as live

    initial = deepcopy(state)
    initial.phase = pb.PHASE_READY
    initial.snapshot_sequence = 1
    state.snapshot_sequence = 2
    replies = [pb.ServerMessage(state=initial), pb.ServerMessage(state=state),
               pb.ServerMessage(readiness=pb.Readiness(ready=True, run_id='live', snapshot_sequence=1))]
    sent = []

    class EndOfStream(Exception):
        pass

    class Connection:
        subprotocol = 'bazaar.protobuf.v2'

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    async def receive(websocket, expected=None):
        if not replies:
            raise EndOfStream
        return replies.pop(0)

    async def send(websocket, command):
        sent.append(command)

    monkeypatch.setattr(live, 'connect', lambda *args, **kwargs: Connection())
    monkeypatch.setattr(live, 'receive', receive)
    monkeypatch.setattr(live, 'send', send)
    with pytest.raises(EndOfStream):
        asyncio.run(live.watch('ws://test', 'test-token', False))
    assert [c.WhichOneof('message') for c in sent] == ['ready', 'advertise', 'offer', 'offer']
    assert sent[0].ready.snapshot_sequence == 1


@pytest.mark.parametrize('specialty', [1, 2, 3])
def test_trades_immediately_with_full_starting_inventory(state, specialty):
    state.tick = 0
    station = getattr(state, 'self')
    station.specialty = specialty
    station.inventory.CopyFrom(pb.Bundle(water=30, food=30, components=30))
    commands = Trader().plan(state)
    offers = [c.offer for c in commands if c.WhichOneof('message') == 'offer']
    assert len(offers) == 2
    assert set(commands[0].advertise.body.seeking.items) == {1, 2, 3} - {specialty}
    for offer in offers:
        assert getattr(offer.body.give, RESOURCES[specialty - 1]) == 3
        assert offer.body.expires_tick == 3


def test_accepts_early_trade_before_stock_is_low(state):
    state.tick = 0
    getattr(state, 'self').inventory.CopyFrom(pb.Bundle(water=30, food=30, components=30))
    incoming(state, components=3)
    assert Trader().plan(state)[0].accept.body.offer_id == 'incoming'


def test_user_snapshot_seeks_food_and_components(state):
    state.tick = 12
    station = getattr(state, 'self')
    station.specialty = pb.RESOURCE_WATER
    station.inventory.CopyFrom(pb.Bundle(water=72, food=18, components=18))
    commands = Trader().plan(state)
    offers = [c.offer for c in commands if c.WhichOneof('message') == 'offer']
    assert len(offers) == 2
    assert any(o.body.receive.food == 3 for o in offers)
    assert any(o.body.receive.components == 3 for o in offers)


def test_stops_buying_when_stock_covers_remaining_run(state):
    state.tick = 115
    getattr(state, 'self').inventory.CopyFrom(pb.Bundle(water=5, food=5, components=30))
    incoming(state, components=3).expires_tick = 119
    commands = Trader().plan(state)
    assert all(c.WhichOneof('message') == 'advertise' for c in commands)
    assert not commands[0].advertise.body.seeking.items


def advertisement(state, identifier, peer, selling, tick=0):
    ad = state.advertisements.items.add(advertisement_id=identifier, station_id=peer,
        created_tick=tick, created_version=tick, expires_tick=tick + 6,
        status=pb.PUBLICATION_STATUS_ACTIVE)
    ad.selling.items.extend(selling)
    return ad


def test_infers_first_ad_and_does_not_count_snapshots_as_new_evidence(state):
    trader = Trader()
    advertisement(state, 'first', 'P01', [pb.RESOURCE_WATER])
    trader.observe(state)
    for _ in range(10):
        trader.observe(state)
    assert trader.inferred_specialties['P01'] == pb.RESOURCE_WATER
    for i in range(3):
        advertisement(state, f'later-{i}', 'P01', [pb.RESOURCE_FOOD], i + 1)
    trader.observe(state)
    assert trader.inferred_specialties['P01'] == pb.RESOURCE_FOOD


def test_ambiguous_selling_does_not_invent_specialty(state):
    trader = Trader()
    advertisement(state, 'mixed', 'P01', [pb.RESOURCE_WATER, pb.RESOURCE_FOOD])
    trader.observe(state)
    assert trader.inferred_specialties['P01'] is None


def test_remembers_expired_ads_and_targets_inferred_producer(state):
    trader = Trader()
    advertisement(state, 'first', 'P02', [pb.RESOURCE_WATER])
    trader.observe(state)
    state.advertisements.ClearField('items')
    offers = [c.offer for c in trader.plan(state) if c.WhichOneof('message') == 'offer']
    assert next(o for o in offers if o.body.receive.water).body.recipient_id == 'P02'
    state.run_id = 'new-run'
    trader.observe(state)
    assert trader.inferred_specialties == {}


def test_observes_new_ads_even_after_planning_this_tick(state):
    trader = Trader()
    trader.plan(state)
    advertisement(state, 'new', 'P02', [pb.RESOURCE_FOOD])
    assert trader.plan(state) == []
    assert trader.inferred_specialties['P02'] == pb.RESOURCE_FOOD


def test_live_compatible_ad_overrides_historical_guess(state):
    trader = Trader()
    advertisement(state, 'old', 'P01', [pb.RESOURCE_WATER])
    ad = advertisement(state, 'current', 'P02', [pb.RESOURCE_WATER], state.tick)
    ad.seeking.items.append(pb.RESOURCE_COMPONENTS)
    offers = [c.offer for c in trader.plan(state) if c.WhichOneof('message') == 'offer']
    assert next(o for o in offers if o.body.receive.water).body.recipient_id == 'P02'
