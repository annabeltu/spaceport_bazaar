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

    def __init__(self, cooperate=False):
        self.cooperate = cooperate
        self.last_tick = None
        self.observation_run = None
        self.advertisement_history = {}
        self.inferred_specialties = {}
        self.last_offer_tick = {}

    def observe(self, state):
        """Infer specialty from the earliest observed advertisement in this run."""
        if self.observation_run != state.run_id:
            self.observation_run = state.run_id
            self.advertisement_history.clear()
            self.inferred_specialties.clear()
            self.last_offer_tick.clear()
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
            selling = set(ads[0][2])
            self.inferred_specialties[peer] = (
                next(iter(selling)) if len(selling) == 1 else None)

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
        contacted = {o.recipient_id for o in outgoing}
        # Rebuild recent opportunities from server-visible offers after reconnect.
        for offer in state.offers.items:
            if offer.proposer_id == state.self_station_id:
                self.last_offer_tick[offer.recipient_id] = max(
                    self.last_offer_tick.get(offer.recipient_id, -1), offer.created_tick)


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
            seekers = {a.station_id for a in state.advertisements.items
                       if a.status == pb.PUBLICATION_STATUS_ACTIVE and a.expires_tick > state.tick
                       and station.specialty in a.seeking.items}
            def rank(peer):
                inferred = self.inferred_specialties.get(peer)
                if peer in sellers:
                    return 0
                if inferred == resource + 1 and peer in seekers:
                    return 1
                if peer in advertised_sellers:
                    return 2
                if inferred == resource + 1:
                    return 3
                return 4 if inferred is None else 5
            candidates = sorted(peers, key=lambda peer: (
                rank(peer), self.last_offer_tick.get(peer, -1) if self.cooperate else 0))
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
                    contacted.add(peer)
                    self.last_offer_tick[peer] = state.tick
                break
        # Preventive aid never assumes access to peer health. Explicit requests
        # outrank likely import needs inferred from a different specialty.
        # Preserve six ticks (or the remaining run) after all pending promises.
        reserve_ticks = min(6, remaining_ticks)
        if (self.cooperate and available[specialty] >= max(0, reserve_ticks - 2) * upkeep[specialty] + 1
                and all(min(inventory[i], available[i] + 2 * upkeep[i]) >= reserve_ticks * upkeep[i]
                        for i in range(3))
                and slots > 0 and ttl):
            aid_peers = {a.station_id for a in state.advertisements.items
                         if a.station_id != state.self_station_id
                         and a.status == pb.PUBLICATION_STATUS_ACTIVE
                         and a.expires_tick > state.tick
                         and station.specialty in a.seeking.items}
            likely_importers = {p for p in peers
                                if self.inferred_specialties.get(p) in (1, 2, 3)
                                and self.inferred_specialties[p] != station.specialty}
            candidates = sorted(peers, key=lambda p: (
                p not in aid_peers, self.last_offer_tick.get(p, -1)))
            peer = next((p for p in candidates
                         if p in aid_peers | likely_importers and p not in contacted), None)
            if peer:
                message, command = _build('offer', pb.OFFER_COMMAND_TYPE_OFFER,
                                          state.run_id, request_id())
                command.body.recipient_id = peer
                command.body.give.CopyFrom(pb.Bundle(**{
                    r: 1 if i == specialty else 0 for i, r in enumerate(RESOURCES)}))
                command.body.receive.CopyFrom(pb.Bundle(water=0, food=0, components=0))
                command.body.expires_tick = state.tick + ttl
                if add(message):
                    self.last_offer_tick[peer] = state.tick
        return commands
