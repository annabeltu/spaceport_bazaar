"""Snapshot-driven survival trading, independent of station identity."""
from uuid import uuid4

from generated import bazaar_pb2 as pb
from client.messages import _build, build_accept, build_advertisement

RESOURCES = ('water', 'food', 'components')


def quantities(bundle):
    return [getattr(bundle, name) for name in RESOURCES]


def describe(state):
    station = getattr(state, 'self')
    specialty = RESOURCES[station.specialty - 1]
    stock = ', '.join(f'{name}={getattr(station.inventory, name)}' for name in RESOURCES)
    status = ' FAILED: a new run is required.' if station.failed_once or station.health == 0 else ''
    return (f'{state.self_station_id}: produces {specialty}; health={station.health}; '
            f'{stock}.{status}')


class Trader:
    """Plan at most one bounded batch per tick; reconstruct holdings on reconnect.

    Keep two ticks of upkeep, seek imports for the remaining run, and
    conservatively budget open outgoing offers even though the server does not
    reserve their inventory. Other players still have to agree to proposed trades.
    """

    def __init__(self):
        self.last_tick = None
        self.observation_run = None
        self.advertisement_history = {}
        self.inferred_specialties = {}

    def observe(self, state):
        """Count distinct publications, not repeated snapshots of the same ad."""
        if self.observation_run != state.run_id:
            self.observation_run = state.run_id
            self.advertisement_history.clear()
            self.inferred_specialties.clear()
        for ad in state.advertisements.items:
            if ad.station_id == state.self_station_id or not ad.advertisement_id:
                continue
            self.advertisement_history[ad.advertisement_id] = (
                ad.station_id, ad.created_tick, ad.created_version, tuple(ad.selling.items))
        histories = {}
        for peer, tick, version, selling in self.advertisement_history.values():
            histories.setdefault(peer, []).append((tick, version, selling))
        for peer, ads in histories.items():
            ads.sort()
            scores = {resource: 0.0 for resource in (1, 2, 3)}
            for index, (_, _, selling) in enumerate(ads):
                if selling:
                    for resource in set(selling):
                        scores[resource] += (2 if index == 0 else 1) / len(set(selling))
            best = max(scores.values())
            winners = [r for r, score in scores.items() if score == best]
            self.inferred_specialties[peer] = winners[0] if best and len(winners) == 1 else None

    def plan(self, state):
        self.observe(state)
        station = getattr(state, 'self')
        key = (state.run_id, state.self_station_id, state.tick)
        if (self.last_tick == key or state.phase != pb.PHASE_RUNNING
                or station.failed_once or station.health == 0):
            return []
        self.last_tick = key
        specialty = station.specialty - 1
        inventory = quantities(station.inventory)
        upkeep = quantities(station.upkeep_per_tick)
        available = [max(0, n - 2 * u) for n, u in zip(inventory, upkeep)]
        remaining_ticks = max(0, state.rules.duration_ticks - state.tick)
        targets = [remaining_ticks * u for u in upkeep]
        needed = [i for i in range(3) if i != specialty and inventory[i] < targets[i]]
        needed.sort(key=lambda i: inventory[i] / max(1, upkeep[i]))
        outgoing = [o for o in state.offers.items if o.proposer_id == state.self_station_id
                    and o.status == pb.OFFER_STATUS_OPEN and o.expires_tick > state.tick]
        for offer in outgoing:
            available = [max(0, n - cost) for n, cost in zip(available, quantities(offer.give))]
        # Stored results also enforce the current tick's budget after reconnect.
        used = sum(r.processed_tick == state.tick for r in state.request_results.items)
        budget = min(state.rules.new_commands_per_station_per_tick - used,
                     state.rules.max_request_records_per_station - len(state.request_results.items))
        commands = []

        def add(message):
            if len(commands) >= budget:
                return False
            if message.ByteSize() > state.rules.max_command_bytes:
                return False
            commands.append(message)
            return True

        def request_id():
            return 'survival-' + uuid4().hex

        incoming = [o for o in state.offers.items if o.recipient_id == state.self_station_id
                    and o.status == pb.OFFER_STATUS_OPEN and o.expires_tick > state.tick]
        incoming.sort(key=lambda o: -sum(min(getattr(o.give, RESOURCES[i]),
                                            max(0, targets[i] - inventory[i])) for i in needed))
        for offer in incoming:
            gain, cost = quantities(offer.give), quantities(offer.receive)
            # Spend only surplus production; never barter away scarce imports.
            if (any(cost[i] for i in range(3) if i != specialty)
                    or any(c > a for c, a in zip(cost, available))
                    or not any(gain[i] > cost[i] for i in needed)
                    or sum(gain[i] for i in needed) < cost[specialty]):
                continue
            if add(build_accept(state.run_id, offer.offer_id, request_id())):
                available = [a - c for a, c in zip(available, cost)]
                inventory = [n + g - c for n, g, c in zip(inventory, gain, cost)]
                needed = [i for i in needed if inventory[i] < targets[i]]

        selling = [station.specialty] if available[specialty] else []
        seeking = [i + 1 for i in needed]
        active = [a for a in state.advertisements.items if a.station_id == state.self_station_id
                  and a.status == pb.PUBLICATION_STATUS_ACTIVE and a.expires_tick > state.tick]
        ttl = min(6, state.rules.max_publication_ttl_ticks)
        if ttl and not any(list(a.selling.items) == selling and list(a.seeking.items) == seeking
                           and a.expires_tick > state.tick + 1 for a in active):
            add(build_advertisement(state.run_id, request_id(), selling, seeking, state.tick + ttl))

        peers = [p.station_id for p in state.directory.items if p.station_id != state.self_station_id]
        if peers:
            offset = state.tick % len(peers)
            peers = peers[offset:] + peers[:offset]
        ttl = min(3, state.rules.max_offer_ttl_ticks)
        slots = state.rules.max_open_outgoing_offers - len(outgoing)
        for resource in needed:
            sellers = {a.station_id for a in state.advertisements.items
                       if a.status == pb.PUBLICATION_STATUS_ACTIVE and a.expires_tick > state.tick
                       and resource + 1 in a.selling.items and station.specialty in a.seeking.items}
            advertised_sellers = {a.station_id for a in state.advertisements.items
                                  if a.status == pb.PUBLICATION_STATUS_ACTIVE
                                  and a.expires_tick > state.tick
                                  and resource + 1 in a.selling.items}
            def rank(peer):
                inferred = self.inferred_specialties.get(peer)
                if peer in sellers:
                    return 0
                if peer in advertised_sellers:
                    return 1
                if inferred == resource + 1:
                    return 2
                return 3 if inferred is None else 4
            candidates = sorted(peers, key=rank)
            # With private specialties and no ads, rotate small probes among peers.
            for peer in candidates:
                if slots <= 0 or not ttl or available[specialty] <= 0:
                    break
                if any(o.recipient_id == peer and getattr(o.receive, RESOURCES[resource])
                       for o in outgoing):
                    continue
                amount = min(3, available[specialty], targets[resource] - inventory[resource])
                message, command = _build('offer', pb.OFFER_COMMAND_TYPE_OFFER, state.run_id, request_id())
                command.body.recipient_id = peer
                give, receive = [0, 0, 0], [0, 0, 0]
                give[specialty] = receive[resource] = amount
                command.body.give.CopyFrom(pb.Bundle(**dict(zip(RESOURCES, give))))
                command.body.receive.CopyFrom(pb.Bundle(**dict(zip(RESOURCES, receive))))
                command.body.expires_tick = state.tick + ttl
                if add(message):
                    available[specialty] -= amount
                    slots -= 1
                break
        return commands
