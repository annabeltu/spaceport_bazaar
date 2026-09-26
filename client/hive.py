#!/usr/bin/env python3
"""Coordinate authorized planet accounts to share supplies through the run."""
import argparse
import asyncio
from contextlib import AsyncExitStack
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from websockets.asyncio.client import connect
from generated import bazaar_pb2 as pb
from client.connection import decode, send
from client.messages import _build, build_accept, build_ready, build_sync, build_withdraw
from client.trading import RESOURCES


def choose_transfer(states):
    """Greedily rescue the shortest runway, preserving the donor's own upkeep.

    Only use observed stock, never promised future production. Imports retain
    thirty ticks of upkeep; a producer can share its specialty above two ticks.
    This is a rolling allocation heuristic, not a guarantee of global feasibility.
    """
    if not states or len({(s.run_id, s.tick) for s in states.values()}) != 1:
        return None
    if any(s.phase != pb.PHASE_RUNNING for s in states.values()):
        return None
    candidates = []
    for recipient, state in states.items():
        station = getattr(state, 'self')
        if station.failed_once or station.health == 0:
            continue
        remaining = max(0, state.rules.duration_ticks - state.tick)
        for index, resource in enumerate(RESOURCES):
            upkeep = getattr(station.upkeep_per_tick, resource)
            stock = getattr(station.inventory, resource)
            if not upkeep:
                continue
            target_ticks = 2 if station.specialty == index + 1 else 30
            target = min(target_ticks, remaining) * upkeep
            deficit = target - stock
            if deficit <= 0:
                continue
            donors = []
            for donor, donor_state in states.items():
                if donor == recipient:
                    continue
                source = getattr(donor_state, 'self')
                if source.failed_once or source.health == 0:
                    continue
                reserve_ticks = 2 if source.specialty == index + 1 else 30
                reserve = min(reserve_ticks, remaining) * getattr(source.upkeep_per_tick, resource)
                surplus = getattr(source.inventory, resource) - reserve
                if surplus > 0:
                    donors.append((surplus, donor))
            if donors:
                surplus, donor = max(donors)
                candidates.append((stock / upkeep, station.health, recipient, donor,
                                   resource, min(deficit, surplus, 10 * upkeep)))
    if not candidates:
        return None
    _, _, recipient, donor, resource, amount = min(candidates)
    return donor, recipient, resource, amount


class Session:
    def __init__(self, websocket, state):
        self.websocket = websocket
        self.state = state
        self.results = {}
        self.changed = asyncio.Condition()
        self.error = None

    async def read(self):
        try:
            while True:
                message = decode(await self.websocket.recv())
                kind = message.WhichOneof('message')
                async with self.changed:
                    if kind == 'state':
                        if message.state.run_id != self.state.run_id:
                            raise RuntimeError('Run changed; restart the coordinator.')
                        self.state = message.state
                    elif kind == 'result':
                        self.results[message.result.request_id] = message.result
                    elif kind == 'protocol_error':
                        raise RuntimeError(f'Server control error: {message.protocol_error.code}')
                    self.changed.notify_all()
        except Exception as error:
            async with self.changed:
                self.error = error
                self.changed.notify_all()

    async def wait_for(self, predicate):
        async with asyncio.timeout(20):
            async with self.changed:
                await self.changed.wait_for(lambda: self.error is not None or predicate())
                if self.error:
                    raise RuntimeError('A planet connection failed; coordination stopped.') from self.error

    async def refresh(self):
        sequence = self.state.snapshot_sequence
        await send(self.websocket, build_sync(self.state.run_id))
        await self.wait_for(lambda: self.state.snapshot_sequence > sequence)

    async def execute(self, message):
        command = getattr(message, message.WhichOneof('message'))
        await send(self.websocket, message)
        await self.wait_for(lambda: command.request_id in self.results)
        result = self.results.pop(command.request_id)
        await self.wait_for(lambda: self.state.world_version >= result.processed_version)
        if not result.ok:
            print(f'{self.state.self_station_id}: command rejected ({pb.ResultCode.Name(result.code)})', flush=True)
        return result


def load_tokens(path):
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or len(data) != 9:
        raise ValueError('Credentials must map nine station IDs to client tokens.')
    if any(not isinstance(k, str) or not isinstance(v, str) or not v.strip() for k, v in data.items()):
        raise ValueError('Station IDs and tokens must be nonempty strings.')
    if len(set(data.values())) != 9:
        raise ValueError('Each planet must have its own distinct client token.')
    return data


def request_id():
    return 'hive-' + uuid4().hex


def has_budget(state, used):
    count = sum(r.processed_tick == state.tick for r in state.request_results.items)
    return (max(count, used.get((state.self_station_id, state.tick), 0))
            < state.rules.new_commands_per_station_per_tick
            and len(state.request_results.items) < state.rules.max_request_records_per_station)


async def run(url, tokens):
    async with AsyncExitStack() as stack:
        sessions = {}
        for station_id, token in tokens.items():
            ws = await stack.enter_async_context(connect(url,
                additional_headers={'Authorization': f'Bearer {token}'},
                subprotocols=['bazaar.protobuf.v2'], open_timeout=20))
            if ws.subprotocol != 'bazaar.protobuf.v2':
                raise RuntimeError('Server did not select the expected protocol.')
            initial = decode(await asyncio.wait_for(ws.recv(), 30))
            if initial.WhichOneof('message') != 'state' or initial.state.self_station_id != station_id:
                raise RuntimeError(f'Credentials did not authenticate as {station_id}.')
            sessions[station_id] = Session(ws, initial.state)
        if len({s.state.run_id for s in sessions.values()}) != 1:
            raise RuntimeError('All planets must belong to the same run.')
        for session in sessions.values():
            state = session.state
            await send(session.websocket, build_ready(state.run_id, state.snapshot_sequence))
            async with asyncio.timeout(30):
                while True:
                    message = decode(await session.websocket.recv())
                    kind = message.WhichOneof('message')
                    if kind == 'state':
                        session.state = message.state
                    elif kind == 'readiness':
                        confirmation = message.readiness
                        if (not confirmation.ready or confirmation.run_id != state.run_id
                                or confirmation.snapshot_sequence != state.snapshot_sequence):
                            raise RuntimeError('Readiness confirmation did not match.')
                        break
                    elif kind == 'protocol_error':
                        raise RuntimeError('Readiness rejected.')
        readers = [asyncio.create_task(s.read()) for s in sessions.values()]
        try:
            await coordinate(sessions)
        finally:
            for task in readers:
                task.cancel()
            await asyncio.gather(*readers, return_exceptions=True)


async def coordinate(sessions):
    used = {}
    last_report = None
    reported_failure = False

    async def execute(station_id, message):
        session = sessions[station_id]
        key = (station_id, session.state.tick)
        count = sum(r.processed_tick == session.state.tick for r in session.state.request_results.items)
        used[key] = max(used.get(key, 0), count) + 1
        return await session.execute(message)

    while True:
        # Refresh after every settlement so no subsequent plan spends stale stock.
        await asyncio.gather(*(s.refresh() for s in sessions.values()))
        states = {peer: s.state for peer, s in sessions.items()}
        ticks = {s.tick for s in states.values()}
        if len(ticks) != 1:
            await asyncio.sleep(0.1)
            continue
        tick = next(iter(ticks))
        used = {key: value for key, value in used.items() if key[1] >= tick}
        if last_report != tick:
            print(f'Tick {tick}: ' + ', '.join(f'{p} health={getattr(s, "self").health}'
                                             for p, s in states.items()), flush=True)
            last_report = tick
        if not reported_failure and any(getattr(s, 'self').failed_once or getattr(s, 'self').health == 0 for s in states.values()):
            reported_failure = True
            print('A planet has permanently failed; all-planet survival now requires a new run.', flush=True)
        if all(s.phase in (pb.PHASE_FINISHED, pb.PHASE_ABORTED) for s in states.values()):
            return
        if any(s.phase != pb.PHASE_RUNNING for s in states.values()):
            await asyncio.sleep(0.5)
            continue
        # Cancel inherited offers before redistributing: these promises could
        # otherwise be accepted by another session and invalidate stock budgets.
        existing = next(((peer, o) for peer, s in states.items() for o in s.offers.items
                         if o.proposer_id == peer and o.status == pb.OFFER_STATUS_OPEN
                         and o.expires_tick > tick), None)
        if existing:
            peer, offer = existing
            if has_budget(states[peer], used):
                await execute(peer, build_withdraw(states[peer].run_id, offer.offer_id, request_id()))
            else:
                await asyncio.sleep(0.5)
            continue
        eligible = {p: s for p, s in states.items() if has_budget(s, used)}
        transfer = choose_transfer(eligible)
        if transfer is None:
            await asyncio.sleep(0.5)
            continue
        donor, recipient, resource, amount = transfer
        state = states[donor]
        ttl = min(3, state.rules.max_offer_ttl_ticks)
        if not ttl or not state.rules.max_open_outgoing_offers:
            await asyncio.sleep(0.5)
            continue
        message, command = _build('offer', pb.OFFER_COMMAND_TYPE_OFFER, state.run_id, request_id())
        command.body.recipient_id = recipient
        command.body.give.CopyFrom(pb.Bundle(**{r: amount if r == resource else 0 for r in RESOURCES}))
        command.body.receive.CopyFrom(pb.Bundle(water=0, food=0, components=0))
        command.body.expires_tick = tick + ttl
        result = await execute(donor, message)
        if result.ok:
            accepted = await execute(recipient, build_accept(state.run_id, result.object_id.value, request_id()))
            if accepted.ok:
                print(f'Settled: {donor} -> {recipient}: {amount} {resource}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='wss://spaceport.edneo.com/ws')
    parser.add_argument('--tokens-file', required=True, help='Private JSON mapping station IDs to nine authorized client tokens')
    args = parser.parse_args()
    try:
        tokens = load_tokens(args.tokens_file)
        asyncio.run(run(args.url, tokens))
    except (ValueError, OSError, RuntimeError, TimeoutError) as error:
        parser.exit(1, f'Coordinator stopped: {type(error).__name__}. Check credentials, connection, and server state.\n')
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
