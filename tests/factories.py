"""
Builders for valid server messages, for tests (package A).

Every builder returns a NEW, complete message: every required field is set,
so IsInitialized() is True and it can be turned into bytes. Changing what one
call returned never affects another call, and any items you pass in are
copied, so changing them afterwards doesn't change the message either.

There are two kinds of builder:
- make_state, make_result, make_readiness and make_protocol_error return the
  OUTER pb.ServerMessage, the way the server sends it. It's ready to pass to
  state.apply_server_message() or engine.decide(). Use `.state`, `.result`,
  `.readiness` or `.protocol_error` to reach the message inside.
- make_offer, make_advertisement and make_transaction return one list ITEM
  (a pb.Offer, and so on), to pass to make_state(offers=[...]) and friends.

Every argument is keyword-only (that's what the `*` in each signature does),
so each call spells out what every value means.

Defaults are the spec's step-1 values wherever the spec gives them. Where it
doesn't, the value is PLACEHOLDER_NUMBER. Package K replaces the `rules`
placeholders with values recorded from the real server.

The IDs here are made up. That's fine in a test: the factory plays the server,
and the server is who names things. The default run ID is the answer keys'
<RUN_ID> stand-in, so messages the client builds from these states can be
compared byte for byte with the answer keys.
"""
from collections.abc import Sequence

from bazaar_client.models import Amounts
from generated import bazaar_pb2 as pb
from spec import SPEC_MAX_COMMAND_BYTES, SPEC_PROTOCOL_VERSION, TEST_PLACEHOLDER_VALUES

# --- Values the spec gives ------------------------------------------------------

TEST_RUN_ID = TEST_PLACEHOLDER_VALUES["RUN_ID"]
OUR_STATION_ID = "P01"  # "your client controls P01"
PEER_STATION_ID = "P02"  # "the server controls P02"
STARTING_INVENTORY = Amounts(water=30, food=30, components=30)  # step 1
NO_RESOURCES = Amounts(water=0, food=0, components=0)
EXERCISE_TICK = 0  # "This exercise stays at tick 0."
SPEC_EXPIRES_TICK = 6  # every command in the spec uses expires_tick: 6
MAX_STORED_RESULTS = 5  # step 9: "only five stored command results"

# --- Placeholders ---------------------------------------------------------------

# For values the spec doesn't give. 999 is deliberately odd, so a placeholder
# is easy to spot if one ever shows up in a failing test.
PLACEHOLDER_NUMBER = 999


# --- Server messages (the outer pb.ServerMessage) --------------------------------


def make_state(
    *,
    run_id: str = TEST_RUN_ID,
    world_version: int = 2,
    snapshot_sequence: int = 1,
    inventory: Amounts = STARTING_INVENTORY,
    imported_total: Amounts = NO_RESOURCES,
    exported_total: Amounts = NO_RESOURCES,
    offers: Sequence[pb.Offer] = (),
    advertisements: Sequence[pb.Advertisement] | None = None,
    transactions: Sequence[pb.Transaction] = (),
    request_results: Sequence[pb.Result] = (),
) -> pb.ServerMessage:
    """A `state` message. With no arguments it's the spec's step-1 state:
    world_version 2, snapshot_sequence 1, station P01 with inventory (30,30,30)
    and specialty water, and P02 advertising food for water.

    `advertisements=None` means that step-1 list; pass `()` for no
    advertisements. Every state in the exercise has tick 0 and phase
    PHASE_RUNNING, and every production, consumption and shortage counter is
    zero, so those are fixed.
    """
    # Why None, not a list, as the default: Python builds a default value ONCE
    # and shares it between calls. A shared protobuf message could be changed
    # by one test and leak into the next. (The Amounts defaults are safe
    # because Amounts is frozen.)
    if advertisements is None:
        advertisements = [_p02_step_1_advertisement()]

    message = pb.ServerMessage()
    state = message.state  # setting a field of `state` below selects it in `message`
    state.type = pb.STATE_TYPE_STATE
    state.protocol_version = SPEC_PROTOCOL_VERSION
    state.run_id = run_id
    state.snapshot_sequence = snapshot_sequence
    state.world_version = world_version
    state.tick = EXERCISE_TICK
    state.phase = pb.PHASE_RUNNING
    state.self_station_id = OUR_STATION_ID
    state.rules.CopyFrom(_placeholder_rules())
    _copy_items_into(state.directory, _directory_entries())
    state.self.CopyFrom(_our_station(inventory, imported_total, exported_total))
    _copy_items_into(state.offers, offers)
    _copy_items_into(state.advertisements, advertisements)
    _copy_items_into(state.transactions, transactions)
    _copy_items_into(state.request_results, request_results)
    state.outcome.null = True  # no outcome until the run ends
    return message


def make_result(
    *,
    run_id: str = TEST_RUN_ID,
    request_id: str = "student-advertise-1",
    ok: bool = True,
    code: int = pb.RESULT_CODE_OK,
    processed_version: int = 3,
    object_id: str | None = None,
    transaction_id: str | None = None,
    retry_after_tick: int | None = None,
) -> pb.ServerMessage:
    """A `result` message. With no arguments it's a success for step 2's
    request_id, processed at step 2's world_version (3).

    The IDs default to null (`{ null: true }`): an ID only exists if you pass
    one, e.g. `object_id=...` for step 3's new advertisement. `code` is a
    pb.ResultCode value.
    """
    message = pb.ServerMessage()
    result = message.result
    result.type = pb.RESULT_TYPE_RESULT
    result.protocol_version = SPEC_PROTOCOL_VERSION
    result.run_id = run_id
    result.request_id = request_id
    result.ok = ok
    result.code = code
    result.processed_tick = EXERCISE_TICK
    result.processed_version = processed_version
    _set_nullable(result.object_id, object_id)
    _set_nullable(result.transaction_id, transaction_id)
    _set_nullable(result.retry_after_tick, retry_after_tick)
    return message


def make_readiness(
    *, run_id: str = TEST_RUN_ID, ready: bool = True, snapshot_sequence: int = 1
) -> pb.ServerMessage:
    """A `readiness` message. With no arguments it confirms step 1's `ready`."""
    message = pb.ServerMessage()
    readiness = message.readiness
    readiness.type = pb.READINESS_TYPE_READINESS
    readiness.protocol_version = SPEC_PROTOCOL_VERSION
    readiness.run_id = run_id
    readiness.ready = ready
    readiness.snapshot_sequence = snapshot_sequence
    return message


def make_protocol_error(
    *,
    run_id: str | None = TEST_RUN_ID,
    request_id: str | None = "student-advertise-2",
    code: int = pb.CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED,
    close_session: bool = False,
) -> pb.ServerMessage:
    """A `protocol_error` message. With no arguments it's step 9's expected
    error: REQUEST_CAPACITY_EXCEEDED for student-advertise-2, and the
    connection stays open.

    Here run_id and request_id are nullable: pass None for `{ null: true }`.
    `code` is a pb.ControlCode value.
    """
    message = pb.ServerMessage()
    error = message.protocol_error
    error.type = pb.PROTOCOL_ERROR_TYPE_PROTOCOL_ERROR
    error.protocol_version = SPEC_PROTOCOL_VERSION
    _set_nullable(error.run_id, run_id)
    _set_nullable(error.request_id, request_id)
    error.code = code
    error.close_session = close_session
    return message


# --- List items, for make_state ------------------------------------------------


def make_offer(
    *,
    offer_id: str,
    proposer_id: str,
    recipient_id: str,
    give: Amounts,
    receive: Amounts,
    status: int = pb.OFFER_STATUS_OPEN,
    created_version: int = PLACEHOLDER_NUMBER,
    expires_tick: int = SPEC_EXPIRES_TICK,
    closed_tick: int | None = None,
    transaction_id: str | None = None,
) -> pb.Offer:
    """One offer, open unless you pass another pb.OfferStatus.

    `give` and `receive` are from the PROPOSER's side, so step 6's gift from
    P02 is give (0,0,1), receive (0,0,0). An accepted offer (step 5) also has
    a closed_tick (0 in this exercise) and the transaction_id of its trade.
    """
    offer = pb.Offer()
    offer.offer_id = offer_id
    offer.proposer_id = proposer_id
    offer.recipient_id = recipient_id
    offer.give.CopyFrom(give.to_bundle())
    offer.receive.CopyFrom(receive.to_bundle())
    offer.created_tick = EXERCISE_TICK
    offer.created_version = created_version
    offer.expires_tick = expires_tick
    offer.status = status
    _set_nullable(offer.closed_tick, closed_tick)
    _set_nullable(offer.transaction_id, transaction_id)
    return offer


def make_advertisement(
    *,
    advertisement_id: str,
    station_id: str,
    selling: Sequence[int],
    seeking: Sequence[int],
    created_version: int = PLACEHOLDER_NUMBER,
    expires_tick: int = SPEC_EXPIRES_TICK,
    status: int = pb.PUBLICATION_STATUS_ACTIVE,
) -> pb.Advertisement:
    """One advertisement, active unless you pass another pb.PublicationStatus.

    `selling` and `seeking` are pb.Resource values; either may be empty, like
    step 3's `selling {}`.
    """
    advertisement = pb.Advertisement()
    advertisement.advertisement_id = advertisement_id
    advertisement.station_id = station_id
    _set_resources(advertisement.selling, selling)
    _set_resources(advertisement.seeking, seeking)
    advertisement.created_tick = EXERCISE_TICK
    advertisement.expires_tick = expires_tick
    advertisement.created_version = created_version
    advertisement.status = status
    return advertisement


def make_transaction(
    *,
    transaction_id: str,
    offer_id: str,
    proposer_id: str,
    recipient_id: str,
    give: Amounts,
    receive: Amounts,
    settled_version: int = PLACEHOLDER_NUMBER,
) -> pb.Transaction:
    """One completed trade. Like an offer, `give` and `receive` are from the
    proposer's side."""
    transaction = pb.Transaction()
    transaction.transaction_id = transaction_id
    transaction.offer_id = offer_id
    transaction.proposer_id = proposer_id
    transaction.recipient_id = recipient_id
    transaction.give.CopyFrom(give.to_bundle())
    transaction.receive.CopyFrom(receive.to_bundle())
    transaction.settled_tick = EXERCISE_TICK
    transaction.settled_version = settled_version
    return transaction


# --- Private helpers ------------------------------------------------------------


def _placeholder_rules():
    """The server's public rules, recorded from a fresh passing run."""
    rules = pb.PublicRules()
    rules.max_command_bytes = SPEC_MAX_COMMAND_BYTES  # "within 16,384 bytes"
    rules.max_request_records_per_station = MAX_STORED_RESULTS

    rules.rules_version = "2.0"
    rules.duration_ticks = 12
    rules.tick_duration_ms = 10_000
    # The order the spec writes bundles in, "(water, food, components)".
    _set_resources(
        rules.resource_order,
        (pb.RESOURCE_WATER, pb.RESOURCE_FOOD, pb.RESOURCE_COMPONENTS),
    )
    rules.max_health = 100
    rules.shortage_damage_per_unit = 5
    rules.recovery_per_fully_supplied_tick = 5
    rules.max_publication_ttl_ticks = 12
    rules.max_offer_ttl_ticks = 12
    rules.new_commands_per_station_per_tick = 10
    rules.max_open_outgoing_offers = 24
    return rules


def _directory_entries():
    """The exercise's two stations, as recorded from the practice server."""
    return [
        pb.DirectoryEntry(station_id=OUR_STATION_ID, display_name="Station P01"),
        pb.DirectoryEntry(station_id=PEER_STATION_ID, display_name="Station P02"),
    ]


def _our_station(inventory, imported_total, exported_total):
    """P01's StationObservation (the state's `self`). No tick has happened,
    so every production, consumption and shortage counter is zero."""
    station = pb.StationObservation()
    station.station_id = OUR_STATION_ID
    station.inventory.CopyFrom(inventory.to_bundle())
    station.health = 100
    station.failed_once = False
    station.first_failure_tick.null = True  # it has never failed
    station.last_production.CopyFrom(NO_RESOURCES.to_bundle())
    station.last_unmet_upkeep.CopyFrom(NO_RESOURCES.to_bundle())
    station.fully_supplied_ticks = 0
    station.shortage_ticks = 0
    station.current_shortage_streak = 0
    station.longest_shortage_streak = 0
    station.produced_total.CopyFrom(NO_RESOURCES.to_bundle())
    station.consumed_total.CopyFrom(NO_RESOURCES.to_bundle())
    station.unmet_total.CopyFrom(NO_RESOURCES.to_bundle())
    station.imported_total.CopyFrom(imported_total.to_bundle())
    station.exported_total.CopyFrom(exported_total.to_bundle())
    upkeep = Amounts(1, 1, 1)
    station.upkeep_per_tick.CopyFrom(upkeep.to_bundle())
    station.specialty = pb.RESOURCE_WATER  # step 1: "self.specialty is RESOURCE_WATER"
    return station


def _p02_step_1_advertisement():
    """Step 1: "P02 has an advertisement selling food and seeking water.\""""
    return make_advertisement(
        advertisement_id="test-p02-advertisement-1",
        station_id=PEER_STATION_ID,
        selling=(pb.RESOURCE_FOOD,),
        seeking=(pb.RESOURCE_WATER,),
        expires_tick=6,
    )


def _copy_items_into(container, items):
    """Put a COPY of each item into a protobuf list container, like `state.offers`.

    SetInParent() marks the container as present even when `items` is empty.
    The spec needs that: `offers {}` (an empty list) is valid, but leaving out
    `offers` is not.
    """
    container.SetInParent()
    for item in items:
        # add() makes a new, empty item inside the list and CopyFrom fills it
        # in. The list gets its own copy, so changing `item` later can't
        # change the message.
        container.items.add().CopyFrom(item)


def _set_resources(container, resources):
    """Fill a list of pb.Resource values (e.g. `selling`), even an empty one."""
    container.SetInParent()
    container.items.extend(resources)


def _set_nullable(wrapper, value):
    """Fill a Nullable* wrapper: `{ value: ... }`, or `{ null: true }` for None.

    The spec: a wrapper must choose exactly one of the two, and `null` must be
    true.
    """
    if value is None:
        wrapper.null = True
    else:
        wrapper.value = value
