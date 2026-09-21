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


def _set_station_defaults(station: pb.StationObservation) -> None:
    station.station_id = "P01"
    _set_bundle(station.inventory, 30, 30, 30)
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
    _set_bundle(station.imported_total, 0, 0, 0)
    _set_bundle(station.exported_total, 0, 0, 0)
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


def build_initial_state(run_id: str, snapshot_sequence: int = 1) -> pb.ServerMessage:
    message = pb.ServerMessage()
    state = message.state
    state.type = pb.STATE_TYPE_STATE
    state.protocol_version = PROTOCOL_VERSION
    state.run_id = run_id
    state.snapshot_sequence = snapshot_sequence
    state.world_version = 2
    state.tick = 0
    state.phase = pb.PHASE_RUNNING
    state.self_station_id = "P01"
    _set_rules(state.rules)

    for station_id, display_name in (("P01", "Planet P01"), ("P02", "Planet P02")):
        entry = state.directory.items.add()
        entry.station_id = station_id
        entry.display_name = display_name

    _set_station_defaults(state.self)
    state.offers.SetInParent()

    advertisement = state.advertisements.items.add()
    advertisement.advertisement_id = "fake-p02-advertisement"
    advertisement.station_id = "P02"
    advertisement.selling.items.append(pb.RESOURCE_FOOD)
    advertisement.seeking.items.append(pb.RESOURCE_WATER)
    advertisement.created_tick = 0
    advertisement.expires_tick = 6
    advertisement.created_version = 1
    advertisement.status = pb.PUBLICATION_STATUS_ACTIVE

    state.transactions.SetInParent()
    state.request_results.SetInParent()
    state.outcome.null = True
    return message


def build_readiness(run_id: str, ready: bool, snapshot_sequence: int) -> pb.ServerMessage:
    message = pb.ServerMessage()
    readiness = message.readiness
    readiness.type = pb.READINESS_TYPE_READINESS
    readiness.protocol_version = PROTOCOL_VERSION
    readiness.run_id = run_id
    readiness.ready = ready
    readiness.snapshot_sequence = snapshot_sequence
    return message

