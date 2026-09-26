import asyncio
import json

import pytest

from generated import bazaar_pb2 as pb
from client.hive import choose_transfer, load_tokens, has_budget, Session
from client.trading import RESOURCES


def world():
    states = {}
    for index in range(9):
        peer = f'P{index + 1:02}'
        state = pb.State(run_id='hive', self_station_id=peer, tick=0, phase=pb.PHASE_RUNNING)
        state.rules.duration_ticks = 120
        state.rules.new_commands_per_station_per_tick = 10
        state.rules.max_request_records_per_station = 2048
        station = getattr(state, 'self')
        station.health = 100
        station.specialty = index % 3 + 1
        station.inventory.CopyFrom(pb.Bundle(water=30, food=30, components=30))
        station.upkeep_per_tick.CopyFrom(pb.Bundle(water=1, food=1, components=1))
        states[peer] = state
    return states


def test_rescues_shortest_supply_runway_and_preserves_donor():
    states = world()
    getattr(states['P02'], 'self').inventory.water = 0
    donor, recipient, resource, amount = choose_transfer(states)
    assert recipient == 'P02' and resource == 'water'
    assert getattr(states[donor], 'self').specialty == pb.RESOURCE_WATER
    assert 0 < amount <= 28


def test_lower_health_breaks_ties():
    states = world()
    for peer in ('P02', 'P03'):
        getattr(states[peer], 'self').inventory.water = 0
    getattr(states['P03'], 'self').health = 10
    assert choose_transfer(states)[1] == 'P03'


def test_no_transfers_for_stale_paused_or_fully_supplied_world():
    states = world()
    assert choose_transfer(states) is None
    getattr(states['P02'], 'self').inventory.water = 0
    states['P01'].tick = 1
    assert choose_transfer(states) is None
    states['P01'].tick = 0
    states['P01'].phase = pb.PHASE_PAUSED
    assert choose_transfer(states) is None


def test_simulated_nine_planets_survive_120_ticks_with_command_limits():
    states = world()
    trades = 0
    for tick in range(120):
        counts = dict.fromkeys(states, 0)
        while True:
            eligible = {p: s for p, s in states.items() if counts[p] < 10}
            transfer = choose_transfer(eligible)
            if transfer is None:
                break
            donor, recipient, resource, amount = transfer
            source = getattr(states[donor], 'self').inventory
            target = getattr(states[recipient], 'self').inventory
            setattr(source, resource, getattr(source, resource) - amount)
            setattr(target, resource, getattr(target, resource) + amount)
            counts[donor] += 1
            counts[recipient] += 1
            trades += 1
        for state in states.values():
            station = getattr(state, 'self')
            specialty = RESOURCES[station.specialty - 1]
            setattr(station.inventory, specialty, getattr(station.inventory, specialty) + 4)
            for resource in RESOURCES:
                stock = getattr(station.inventory, resource)
                assert stock >= 1, (tick, state.self_station_id, resource)
                setattr(station.inventory, resource, stock - 1)
            state.tick = tick + 1
    assert trades > 0
    assert all(getattr(s, 'self').health == 100 and s.tick == 120 for s in states.values())


def test_credentials_require_nine_distinct_tokens(tmp_path):
    path = tmp_path / 'tokens.json'
    path.write_text(json.dumps({'P01': 'one'}))
    with pytest.raises(ValueError):
        load_tokens(path)
    path.write_text(json.dumps({p: p + '-token' for p in world()}))
    assert len(load_tokens(path)) == 9


def test_budget_accounts_for_local_and_server_commands():
    state = world()['P01']
    assert has_budget(state, {})
    assert not has_budget(state, {('P01', 0): 10})
    for _ in range(10):
        state.request_results.items.add(processed_tick=0)
    assert not has_budget(state, {})


def test_producers_do_not_pass_their_reserve_back_and_forth():
    states = world()
    getattr(states['P01'], 'self').inventory.water = 2
    assert choose_transfer(states) is None


def test_execute_waits_for_result_and_authoritative_state(monkeypatch):
    import client.hive as hive
    from copy import deepcopy

    async def scenario():
        initial = world()['P01']
        initial.world_version = 1
        initial.snapshot_sequence = 1
        session = Session(object(), initial)
        message = hive.build_accept('hive', 'offer-1', 'test-accept')
        sent = []

        async def send(ws, command):
            sent.append(command)

        monkeypatch.setattr(hive, 'send', send)
        task = asyncio.create_task(session.execute(message))
        await asyncio.sleep(0)
        async with session.changed:
            session.results['test-accept'] = pb.Result(request_id='test-accept', ok=True,
                processed_version=2)
            session.changed.notify_all()
        await asyncio.sleep(0)
        assert not task.done()
        async with session.changed:
            session.state = deepcopy(initial)
            session.state.world_version = 2
            session.changed.notify_all()
        assert (await task).ok
        assert len(sent) == 1

    asyncio.run(scenario())
