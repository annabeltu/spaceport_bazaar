"""Protobuf builders for the independent fake server.

The builders are deliberately explicit. Bazaar uses proto2, where required zero and
false values still have to be assigned for a message to be valid.
"""

from generated import bazaar_pb2 as pb


PROTOCOL_VERSION = "2.0"


def _set_bundle(bundle: pb.Bundle, water: int, food: int, components: int) -> None:
    bundle.water = water
    bundle.food = food
    bundle.components = components


def _set_null_uint(value: pb.NullableUint) -> None:
    value.null = True


def _set_station_defaults(station: pb.StationObservation, world_version: int) -> None:
    station.station_id = "P01"
    food = 31 if world_version >= 6 else 30
    components = 31 if world_version >= 8 else 30
    water = 28 if world_version >= 6 else 30
    _set_bundle(station.inventory, water, food, components)
    station.health = 100
    station.failed_once = False
    _set_null_uint(station.first_failure_tick)
    _set_bundle(station.last_production, 0, 0, 0)
    _set_bundle(station.last_unmet_upkeep, 0, 0, 0)
    station.fully_supplied_ticks = 0
    station.shortage_ticks = 0
    station.current_shortage_streak = 0
    station.longest_shortage_streak = 0
    _set_bundle(station.produced_total, 0, 0, 0)
    _set_bundle(station.consumed_total, 0, 0, 0)
    _set_bundle(station.unmet_total, 0, 0, 0)
    _set_bundle(
        station.imported_total,
        0,
        1 if world_version >= 6 else 0,
        1 if world_version >= 8 else 0,
    )
    _set_bundle(station.exported_total, 2 if world_version >= 6 else 0, 0, 0)
    _set_bundle(station.upkeep_per_tick, 0, 0, 0)
    station.specialty = pb.RESOURCE_WATER


def _set_rules(rules: pb.PublicRules) -> None:
    # UNVERIFIED: the public spec gives only these two limits. Package K will
    # compare the remaining harmless placeholders with real-server recordings.
    rules.rules_version = "practice-v2"
    rules.duration_ticks = 6
    rules.tick_duration_ms = 1_000
    rules.resource_order.items.extend(
        [pb.RESOURCE_WATER, pb.RESOURCE_FOOD, pb.RESOURCE_COMPONENTS]
    )
    rules.max_health = 100
    rules.shortage_damage_per_unit = 1
    rules.recovery_per_fully_supplied_tick = 1
    rules.max_publication_ttl_ticks = 6
    rules.max_offer_ttl_ticks = 6
    rules.new_commands_per_station_per_tick = 10
    rules.max_request_records_per_station = 5
    rules.max_open_outgoing_offers = 10
    rules.max_command_bytes = 16_384


def _add_advertisement(
    state: pb.State,
    advertisement_id: str,
    station_id: str,
    selling: tuple[int, ...],
    seeking: tuple[int, ...],
    created_version: int,
) -> None:
    advertisement = state.advertisements.items.add()
    advertisement.advertisement_id = advertisement_id
    advertisement.station_id = station_id
    advertisement.selling.items.extend(selling)
    advertisement.seeking.items.extend(seeking)
    advertisement.created_tick = 0
    advertisement.expires_tick = 6
    advertisement.created_version = created_version
    advertisement.status = pb.PUBLICATION_STATUS_ACTIVE


def _add_offer(
    state: pb.State,
    offer_id: str,
    proposer_id: str,
    recipient_id: str,
    give: tuple[int, int, int],
    receive: tuple[int, int, int],
    created_version: int,
    accepted: bool,
    transaction_id: str,
) -> None:
    offer = state.offers.items.add()
    offer.offer_id = offer_id
    offer.proposer_id = proposer_id
    offer.recipient_id = recipient_id
    _set_bundle(offer.give, *give)
    _set_bundle(offer.receive, *receive)
    offer.created_tick = 0
    offer.created_version = created_version
    offer.expires_tick = 6
    offer.status = pb.OFFER_STATUS_ACCEPTED if accepted else pb.OFFER_STATUS_OPEN
    if accepted:
        offer.closed_tick.value = 0
        offer.transaction_id.value = transaction_id
    else:
        offer.closed_tick.null = True
        offer.transaction_id.null = True


def _add_transaction(
    state: pb.State,
    transaction_id: str,
    offer_id: str,
    proposer_id: str,
    recipient_id: str,
    give: tuple[int, int, int],
    receive: tuple[int, int, int],
    settled_version: int,
) -> None:
    transaction = state.transactions.items.add()
    transaction.transaction_id = transaction_id
    transaction.offer_id = offer_id
    transaction.proposer_id = proposer_id
    transaction.recipient_id = recipient_id
    _set_bundle(transaction.give, *give)
    _set_bundle(transaction.receive, *receive)
    transaction.settled_tick = 0
    transaction.settled_version = settled_version


def _set_directory(state: pb.State) -> None:
    for station_id, display_name in (("P01", "Planet P01"), ("P02", "Planet P02")):
        entry = state.directory.items.add()
        entry.station_id = station_id
        entry.display_name = display_name


def _set_advertisements(state: pb.State, world_version: int) -> None:
    # UNVERIFIED: the spec doesn't publish object-ID formats. Package K replaces
    # these deterministic fake IDs if recordings reveal a useful convention.
    _add_advertisement(
        state,
        "fake-p02-advertisement",
        "P02",
        (pb.RESOURCE_FOOD,),
        (pb.RESOURCE_WATER,),
        1,
    )
    if 3 <= world_version <= 8:
        first = world_version == 3
        _add_advertisement(
            state,
            "fake-p01-advertisement-1" if first else "fake-p01-advertisement-2",
            "P01",
            (pb.RESOURCE_WATER,) if first else (),
            (pb.RESOURCE_FOOD,) if first else (pb.RESOURCE_COMPONENTS,),
            3 if first else 4,
        )


def _set_offers(state: pb.State, world_version: int) -> None:
    if world_version >= 5:
        _add_offer(
            state,
            "fake-p01-offer-1",
            "P01",
            "P02",
            (2, 0, 0),
            (0, 1, 0),
            5,
            world_version >= 6,
            "fake-transaction-1",
        )
    if world_version >= 7:
        _add_offer(
            state,
            "fake-p02-gift-1",
            "P02",
            "P01",
            (0, 0, 1),
            (0, 0, 0),
            7,
            world_version >= 8,
            "fake-transaction-2",
        )


def _set_transactions(state: pb.State, world_version: int) -> None:
    if world_version >= 6:
        _add_transaction(
            state,
            "fake-transaction-1",
            "fake-p01-offer-1",
            "P01",
            "P02",
            (2, 0, 0),
            (0, 1, 0),
            6,
        )
    if world_version >= 8:
        _add_transaction(
            state,
            "fake-transaction-2",
            "fake-p02-gift-1",
            "P02",
            "P01",
            (0, 0, 1),
            (0, 0, 0),
            8,
        )


def build_state(
    run_id: str,
    snapshot_sequence: int,
    world_version: int,
    stored_results: tuple[bytes, ...] = (),
) -> pb.ServerMessage:
    message = pb.ServerMessage()
    state = message.state
    state.type = pb.STATE_TYPE_STATE
    state.protocol_version = PROTOCOL_VERSION
    state.run_id = run_id
    state.snapshot_sequence = snapshot_sequence
    state.world_version = world_version
    state.tick = 0
    state.phase = pb.PHASE_RUNNING
    state.self_station_id = "P01"
    _set_rules(state.rules)
    _set_directory(state)
    _set_station_defaults(state.self, world_version)
    _set_advertisements(state, world_version)
    _set_offers(state, world_version)
    _set_transactions(state, world_version)
    for result_bytes in stored_results:
        state.request_results.items.add().ParseFromString(result_bytes)
    state.offers.SetInParent()
    state.advertisements.SetInParent()
    state.transactions.SetInParent()
    state.request_results.SetInParent()
    state.outcome.null = True
    return message


def build_initial_state(run_id: str, snapshot_sequence: int = 1) -> pb.ServerMessage:
    return build_state(run_id, snapshot_sequence, world_version=2)


def build_readiness(run_id: str, ready: bool, snapshot_sequence: int) -> pb.ServerMessage:
    message = pb.ServerMessage()
    readiness = message.readiness
    readiness.type = pb.READINESS_TYPE_READINESS
    readiness.protocol_version = PROTOCOL_VERSION
    readiness.run_id = run_id
    readiness.ready = ready
    readiness.snapshot_sequence = snapshot_sequence
    return message


def build_result(
    run_id: str,
    request_id: str,
    processed_version: int,
    *,
    object_id: str | None = None,
    transaction_id: str | None = None,
    ok: bool = True,
    code: int = pb.RESULT_CODE_OK,
) -> pb.ServerMessage:
    message = pb.ServerMessage()
    result = message.result
    result.type = pb.RESULT_TYPE_RESULT
    result.protocol_version = PROTOCOL_VERSION
    result.run_id = run_id
    result.request_id = request_id
    result.ok = ok
    result.code = code
    result.processed_tick = 0
    result.processed_version = processed_version
    if object_id is None:
        result.object_id.null = True
    else:
        result.object_id.value = object_id
    if transaction_id is None:
        result.transaction_id.null = True
    else:
        result.transaction_id.value = transaction_id
    result.retry_after_tick.null = True
    return message


def build_protocol_error(
    run_id: str | None,
    request_id: str | None,
    code: int,
    close_session: bool,
) -> pb.ServerMessage:
    message = pb.ServerMessage()
    error = message.protocol_error
    error.type = pb.PROTOCOL_ERROR_TYPE_PROTOCOL_ERROR
    error.protocol_version = PROTOCOL_VERSION
    if run_id is None:
        error.run_id.null = True
    else:
        error.run_id.value = run_id
    if request_id is None:
        error.request_id.null = True
    else:
        error.request_id.value = request_id
    error.code = code
    error.close_session = close_session
    return message
