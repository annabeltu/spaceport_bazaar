"""
Decides the client's next move (contract and decision types by package A,
Position's fields and the functions by package H).

The engine is pure logic: no network, no clock, no printing. Given what just
arrived, it returns where we are now and what to do next. That makes every
case a fast unit test.

It's SCRIPTED on purpose: it plays exactly the spec's 10-step practice
exercise (bazaar-protobuf-starter-linux/README.md, "Complete the exchange")
and nothing else. The classroom game needs a real strategy, which gets its own
plan once the rules are published.

How it works:
- `_STEPS` (at the bottom) is the spec as a table. For each step it lists the
  command we send, the replies the spec says come back, and the values those
  replies must show.
- A Position records which step we're on and how many of its replies have
  arrived. decide() checks each message against the table. Any difference is
  a Stop with the reason: a client that guesses can end the exercise with a
  "scenario mismatch".
- Readiness sits on top of the script. Whenever the client isn't ready on the
  current connection, the next move is `ready` (or Wait, if one is already on
  its way). Once the server confirms it, the script carries on where it
  stopped, including an exact retry of a command whose result never arrived.
"""
import dataclasses
from collections.abc import Callable
from dataclasses import dataclass

from generated import bazaar_pb2 as pb

from bazaar_client import commands
from bazaar_client.models import Amounts, ClientState

# --- The spec's values ---------------------------------------------------------

_OUR_STATION = "P01"  # "your client controls P01"
_PEER_STATION = "P02"  # "the server controls P02"
_LAST_STEP = 10
_EXPIRES_TICK = 6  # every command in the spec uses expires_tick: 6
_START = Amounts(water=30, food=30, components=30)  # steps 1-4
_AFTER_TRADE = Amounts(water=28, food=31, components=30)  # steps 5-6: paid 2 water, got 1 food
_AFTER_GIFT = Amounts(water=28, food=31, components=31)  # steps 7-10: plus 1 component
_NOTHING = Amounts(water=0, food=0, components=0)
_OFFER_GIVE = Amounts(water=2, food=0, components=0)  # step 4: "two water ...
_OFFER_RECEIVE = Amounts(water=0, food=1, components=0)  # ... for one food"
_GIFT = Amounts(water=0, food=0, components=1)  # step 6: "one component"
# Step 10: "the five stored command results. You imported one food and one
# component ... and exported two water."
_STORED_RESULTS = 5
_IMPORTED_TOTAL = Amounts(water=0, food=1, components=1)
_EXPORTED_TOTAL = Amounts(water=2, food=0, components=0)
# Step 10: "Production, consumption, and shortage counters are zero because no
# simulation tick occurred." These are the fields of `self` that count those.
_ZERO_BUNDLES = (
    "last_production", "produced_total", "consumed_total", "last_unmet_upkeep", "unmet_total"
)
_ZERO_COUNTS = ("shortage_ticks", "current_shortage_streak", "longest_shortage_streak")
# Step 9's command deliberately gets a protocol_error, which the server never
# stores. The spec: "continue to sync instead of retrying that command".
_NO_RETRY_STEP = 9


@dataclass(frozen=True)
class Position:
    """Where we are in the practice script.

    Everyone else treats a Position as opaque: the runner just keeps the
    latest one decide() returns and passes it back in with the next message.

    step:                the spec step (1-10) whose replies we're reading.
                         11 means the script is finished.
    replies_seen:        how many of that step's replies have arrived so far.
    run_id:              the run, from step 1's state. None before it.
    last_sequence:       snapshot_sequence of the latest state on this
                         connection, so the next one can be checked.
    ready_pending:       we sent `ready` and its `readiness` reply hasn't come.
    advertisement_id:    step 3's result.object_id (the spec's ADVERTISEMENT_ID).
    our_offer_id:        our step 4 offer, as listed in step 4's state.
    zero_price_offer_id: P02's step 6 gift (the spec's ZERO_PRICE_OFFER_ID).

    The IDs are always copied from the server's messages, never made up.
    """

    step: int
    replies_seen: int
    run_id: str | None
    last_sequence: int
    ready_pending: bool
    advertisement_id: str | None
    our_offer_id: str | None
    zero_price_offer_id: str | None


# The four possible decisions. Each is a frozen dataclass too, so a decision
# can't change after the engine makes it.


@dataclass(frozen=True)
class Send:
    """Send `message` next. The runner runs it through guards.check() first.

    `message` is a NEW message that nothing else holds, so nobody can change
    it between the decision and the send.
    """

    message: pb.ClientMessage


@dataclass(frozen=True)
class Wait:
    """Nothing to send yet: keep reading. For example, steps 4-6 deliver one
    result followed by three states with no command in between."""


@dataclass(frozen=True)
class Finish:
    """The script is complete and every final check passed. The runner logs
    `summary`, closes the connection and exits with 0."""

    summary: str


@dataclass(frozen=True)
class Stop:
    """Something didn't match the spec, so the client must not guess. The
    runner sends nothing more, logs `reason`, closes the connection and exits
    with a non-zero code."""

    reason: str


# decide() returns exactly one of these. Check which with isinstance(), e.g.
# `if isinstance(decision, Send):`, or with a `match` statement.
Decision = Send | Wait | Finish | Stop


def first_position() -> Position:
    """Where the script starts: nothing received yet, waiting for step 1's state."""
    return Position(
        step=1,
        replies_seen=0,
        run_id=None,
        last_sequence=0,
        ready_pending=False,
        advertisement_id=None,
        our_offer_id=None,
        zero_price_offer_id=None,
    )


def decide(
    client: ClientState, position: Position, received: pb.ServerMessage
) -> tuple[Position, Decision]:
    """The new position and the next move, after `received` arrived.

    `client` already includes `received`: the runner calls
    state.apply_server_message() first, then decide(). decide() never changes
    `client`, `position` or `received`.

    Rules from the plan (package H):
    - Send a step only after every reply the spec lists for the previous step
      has arrived.
    - Check each step's expected values from the spec (world_version,
      snapshot_sequence, inventory, offer status). On any mismatch return
      Stop with a clear reason. Never guess.
    - IDs come from the server and are never made up.
    - Step 9's protocol_error with REQUEST_CAPACITY_EXCEEDED for
      student-advertise-2 is expected, so carry on to sync. Any other
      protocol_error means Stop.
    - When client.is_ready is False (a new connection), the next move is
      `ready` with that connection's snapshot_sequence, then an exact retry of
      any command whose result never arrived. If client.sent_ready_sequence
      isn't None, a `ready` is already waiting for its reply, so Wait instead
      of sending another.
    """
    try:
        return _decide(client, position, received)
    except _Mismatch as mismatch:
        # Keep the old position: after a Stop nothing more is acted on.
        where = f"step {position.step}" if position.step <= _LAST_STEP else "after step 10"
        return position, Stop(f"{where}: {mismatch}")


class _Mismatch(Exception):
    """A message doesn't match the spec. decide() turns it into a Stop.

    Raising this from inside a check, instead of returning an error through
    every function, lets each check read as a plain list of requirements.
    """


def _require(condition: bool, reason: str) -> None:
    """Raise _Mismatch(reason) unless `condition` holds."""
    if not condition:
        raise _Mismatch(reason)


def _decide(client: ClientState, position: Position, received: pb.ServerMessage):
    """decide() without the Stop handling: raises _Mismatch instead."""
    kind = received.WhichOneof("message")
    what = _describe(received)
    _require(position.step <= _LAST_STEP, f"the script already finished, but {what} arrived")
    # The server starts every connection with a state ("Read the first
    # state"), and our `ready` must carry that state's snapshot_sequence.
    _require(
        kind == "state" or not _is_new_connection(client),
        f"a new connection must start with a state, but {what} arrived",
    )
    if kind == "readiness":
        return _on_readiness(client, position, received.readiness)
    if kind == "state":
        position = _on_state(client, position, received.state)
    else:
        expected = _expected_reply(position)
        _require(kind == expected, f"expected a {expected}, but {what} arrived")
        if kind == "result":
            position = _on_result(position, received.result)
        else:
            position = _on_protocol_error(position, received.protocol_error, what)
    return _next_move(client, position)


def _is_new_connection(client: ClientState) -> bool:
    """Not ready, and no `ready` sent yet: true at the very start, and after
    the runner calls state.on_new_connection() for a reconnect."""
    return not client.is_ready and client.sent_ready_sequence is None


def _expected_reply(position: Position) -> str:
    """The kind of message the script is waiting for, e.g. "result"."""
    return _STEPS[position.step].replies[position.replies_seen]


def _advance(position: Position) -> Position:
    """`position` after one more reply of the current step arrived. After the
    step's last reply, it moves on to the next step."""
    replies_seen = position.replies_seen + 1
    if replies_seen < len(_STEPS[position.step].replies):
        return dataclasses.replace(position, replies_seen=replies_seen)
    return dataclasses.replace(position, step=position.step + 1, replies_seen=0)


def _next_move(client: ClientState, position: Position) -> tuple[Position, Decision]:
    """What to do now that `position` is up to date."""
    if position.step > _LAST_STEP:
        return position, Finish(_summary(client))
    if not client.is_ready:
        if client.sent_ready_sequence is not None:
            return position, Wait()  # our `ready` is still waiting for its reply
        # "Use its run ID and snapshot_sequence": the latest state's, which is
        # this connection's (the new-connection check in _decide ensures it).
        ready = commands.build_ready(client.run_id, client.snapshot.snapshot_sequence, True)
        return dataclasses.replace(position, ready_pending=True), Send(ready)
    step = _STEPS[position.step]
    if position.replies_seen > 0 or step.command is None:
        return position, Wait()  # the server still owes us replies
    if position.step == _NO_RETRY_STEP and step.request_id in client.sent_requests:
        # A reconnect lost step 9's error. Don't retry it: go straight to sync.
        return _next_move(client, dataclasses.replace(position, step=position.step + 1))
    # Either the step's first send or, after a reconnect, an exact retry: the
    # same inputs build the same message, so both are the same bytes.
    return position, Send(step.command(client.run_id, step.request_id, position))


def _summary(client: ClientState) -> str:
    """Finish's summary: what the final checks confirmed."""
    inventory = _show(Amounts.from_bundle(client.snapshot.self.inventory))
    return (
        f"practice script complete: all {_LAST_STEP} steps matched the spec; final "
        f"inventory {inventory}, 2 transactions, {_STORED_RESULTS} stored results"
    )


# --- Handling each kind of message ---------------------------------------------


def _on_readiness(client: ClientState, position: Position, readiness: pb.Readiness):
    """The server's reply to our `ready`."""
    _require(position.ready_pending, "a readiness arrived, but no ready is waiting for a reply")
    # state.py sets is_ready only when the reply matches our `ready` exactly:
    # ready true, our run_id, and the snapshot_sequence our `ready` carried.
    run_id = "matches" if readiness.run_id == client.run_id else "differs"
    _require(
        client.is_ready,
        f"the readiness reply doesn't match our ready: ready={readiness.ready}, "
        f"snapshot_sequence={readiness.snapshot_sequence} (ours was "
        f"{client.sent_ready_sequence}), run_id {run_id}",
    )
    return _next_move(client, dataclasses.replace(position, ready_pending=False))


def _on_state(client: ClientState, position: Position, state: pb.State) -> Position:
    """Check a state and return the position after it."""
    new_connection = _is_new_connection(client)
    _check_state_basics(position, state, new_connection)
    position = dataclasses.replace(
        position, run_id=state.run_id, last_sequence=state.snapshot_sequence
    )
    expected = _expected_reply(position)
    if expected != "state":
        # A new connection starts with the server's current state. If the
        # script was waiting for a result or error, that reply was lost with
        # the old connection, so this state isn't one of the spec's replies.
        # Readiness comes next, then the lost command again (_next_move).
        _require(new_connection, f"expected a {expected}, but a state arrived")
        return position
    step = _STEPS[position.step]
    _require(
        state.world_version == step.world_version,
        f"world_version is {state.world_version}, the spec says {step.world_version}",
    )
    _require_amounts("inventory", state.self.inventory, step.inventory)
    if step.transactions is not None:
        count = len(state.transactions.items)
        _require(
            count == step.transactions,
            f"there are {count} transactions, the spec says {step.transactions}",
        )
    return _advance(step.check_state(position, state))


def _check_state_basics(position: Position, state: pb.State, new_connection: bool) -> None:
    """What every state must show, whichever step it belongs to."""
    if position.run_id is not None:
        _require(
            state.run_id == position.run_id,
            "a state from a different run arrived (the server restarted?). Our saved IDs "
            "mean nothing in a new run, so restart the client too",
        )
    # "snapshot_sequence counts state messages on this connection: 1, 2, 3,
    # and so on ... and starts over at 1 on a new connection." On one
    # connection that gives exactly the spec's per-step numbers, 1 to 9.
    expected_sequence = 1 if new_connection else position.last_sequence + 1
    _require(
        state.snapshot_sequence == expected_sequence,
        f"snapshot_sequence is {state.snapshot_sequence}, expected {expected_sequence}",
    )
    # "Every state in this exercise has tick: 0 and phase: PHASE_RUNNING."
    _require(state.tick == 0, f"tick is {state.tick}, the spec says 0")
    _require(
        state.phase == pb.PHASE_RUNNING,
        f"phase is {pb.Phase.Name(state.phase)}, the spec says PHASE_RUNNING",
    )
    _require(
        state.self_station_id == _OUR_STATION,
        f"self_station_id is {state.self_station_id!r}, expected {_OUR_STATION!r}",
    )


def _on_result(position: Position, result: pb.Result) -> Position:
    """Check a command's result and return the position after it."""
    step = _STEPS[position.step]
    _require(result.run_id == position.run_id, "a result from a different run arrived")
    _require(
        result.request_id == step.request_id,
        f"the result is for {result.request_id!r}, expected {step.request_id!r}",
    )
    _require(
        result.ok and result.code == pb.RESULT_CODE_OK,
        f"{step.request_id} failed: ok={result.ok}, code={pb.ResultCode.Name(result.code)}",
    )
    if step.check_result is not None:
        position = step.check_result(position, result)
    return _advance(position)


def _on_protocol_error(position: Position, error: pb.ProtocolError, what: str) -> Position:
    """Step 9's deliberate error. _decide already checked the script expects one."""
    request_id = _STEPS[position.step].request_id
    _require(
        error.code == pb.CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED
        and not error.close_session
        and _nullable_value(error.request_id) == request_id,
        f"expected REQUEST_CAPACITY_EXCEEDED for {request_id!r} with close_session "
        f"false, but {what} arrived",
    )
    return _advance(position)


# --- Small helpers -------------------------------------------------------------


def _describe(received: pb.ServerMessage) -> str:
    """A short description of a message, for Stop reasons."""
    kind = received.WhichOneof("message")
    if kind is None:
        return "a message with nothing selected"
    if kind != "protocol_error":
        return f"a {kind}"
    error = received.protocol_error
    return (
        f"a protocol_error ({pb.ControlCode.Name(error.code)}, close_session="
        f"{error.close_session}, request_id={_nullable_value(error.request_id)!r})"
    )


def _nullable_value(wrapper):
    """The value inside a Nullable* wrapper, or None for `{ null: true }`."""
    return wrapper.value if wrapper.WhichOneof("kind") == "value" else None


def _show(amounts: Amounts) -> str:
    """Amounts written the way the spec writes them, e.g. "(28,31,31)"."""
    return f"({amounts.water},{amounts.food},{amounts.components})"


def _require_amounts(name: str, bundle: pb.Bundle, expected: Amounts) -> None:
    """Require a protobuf Bundle to hold exactly `expected`."""
    actual = Amounts.from_bundle(bundle)
    _require(actual == expected, f"{name} is {_show(actual)}, the spec says {_show(expected)}")


def _names(resources) -> list[str]:
    """Resource numbers as names, e.g. ["RESOURCE_WATER"], for Stop reasons."""
    return [pb.Resource.Name(resource) for resource in resources]


def _our_only_active_ad(state: pb.State, selling: tuple, seeking: tuple) -> pb.Advertisement:
    """Our one active advertisement, checked against what we advertised. The
    spec: "each planet has at most one active listing"."""
    ours = [
        ad for ad in state.advertisements.items
        if ad.station_id == _OUR_STATION and ad.status == pb.PUBLICATION_STATUS_ACTIVE
    ]
    _require(len(ours) == 1, f"expected 1 active advertisement from P01, found {len(ours)}")
    ad = ours[0]
    _require(
        tuple(ad.selling.items) == selling
        and tuple(ad.seeking.items) == seeking
        and ad.expires_tick == _EXPIRES_TICK,
        f"our advertisement sells {_names(ad.selling.items)} and seeks "
        f"{_names(ad.seeking.items)} until tick {ad.expires_tick}; we sent "
        f"{_names(selling)}, {_names(seeking)} and {_EXPIRES_TICK}",
    )
    return ad


def _require_status(offer: pb.Offer, status: int) -> None:
    """Require an offer to have the given pb.OfferStatus."""
    _require(
        offer.status == status,
        f"offer {offer.offer_id!r} is {pb.OfferStatus.Name(offer.status)}, the spec says "
        f"{pb.OfferStatus.Name(status)}",
    )


# --- Each step's own checks ----------------------------------------------------
# Each takes the position and the message and returns the position, with any
# ID the message names saved in it. Checks that save nothing return it as-is.


def _check_step_1(position: Position, state: pb.State) -> Position:
    """"self.specialty is RESOURCE_WATER. P02 has an advertisement selling food
    and seeking water." (Station, inventory and counters are checked elsewhere.)"""
    specialty = pb.Resource.Name(state.self.specialty)
    _require(
        state.self.specialty == pb.RESOURCE_WATER,
        f"self.specialty is {specialty}, the spec says RESOURCE_WATER",
    )
    _require(
        any(
            ad.station_id == _PEER_STATION
            and ad.status == pb.PUBLICATION_STATUS_ACTIVE
            and pb.RESOURCE_FOOD in ad.selling.items
            and pb.RESOURCE_WATER in ad.seeking.items
            for ad in state.advertisements.items
        ),
        "P02 has no active advertisement selling food and seeking water",
    )
    return position


def _check_step_2(position: Position, state: pb.State) -> Position:
    """"Your advertisement appears in advertisements.items.\""""
    _our_only_active_ad(state, (pb.RESOURCE_WATER,), (pb.RESOURCE_FOOD,))
    return position


def _check_step_3_result(position: Position, result: pb.Result) -> Position:
    """"Save result.object_id.value as ADVERTISEMENT_ID.\""""
    advertisement_id = _nullable_value(result.object_id)
    _require(advertisement_id, "the result has no object_id, so there's no ADVERTISEMENT_ID")
    return dataclasses.replace(position, advertisement_id=advertisement_id)


def _check_advertisement_seeking_components(position: Position, state: pb.State) -> Position:
    """Step 3: "Your new advertisement sells nothing and seeks components. It
    replaces your first advertisement." Step 7: it "is still active"."""
    ad = _our_only_active_ad(state, (), (pb.RESOURCE_COMPONENTS,))
    _require(
        ad.advertisement_id == position.advertisement_id,
        f"our active advertisement is {ad.advertisement_id!r}, but step 3's result "
        f"named {position.advertisement_id!r}",
    )
    return position


def _check_step_4(position: Position, state: pb.State) -> Position:
    """"Your offer to P02 appears in offers.items with status: OFFER_STATUS_OPEN
    ... Save the offer's ID to track it.\""""
    ours = [
        offer for offer in state.offers.items
        if offer.proposer_id == _OUR_STATION
        and offer.recipient_id == _PEER_STATION
        and Amounts.from_bundle(offer.give) == _OFFER_GIVE
        and Amounts.from_bundle(offer.receive) == _OFFER_RECEIVE
    ]
    _require(len(ours) == 1, f"expected 1 offer to P02 of 2 water for 1 food, found {len(ours)}")
    _require_status(ours[0], pb.OFFER_STATUS_OPEN)
    return dataclasses.replace(position, our_offer_id=ours[0].offer_id)


def _check_step_5(position: Position, state: pb.State) -> Position:
    """"Your offer is now OFFER_STATUS_ACCEPTED.\""""
    ours = [offer for offer in state.offers.items if offer.offer_id == position.our_offer_id]
    _require(len(ours) == 1, f"our offer {position.our_offer_id!r} is missing from offers.items")
    _require_status(ours[0], pb.OFFER_STATUS_ACCEPTED)
    return position


def _check_step_6(position: Position, state: pb.State) -> Position:
    """"Find the open offer from P02 to P01. Its give bundle contains one
    component, and its receive bundle is all zeros. Save its offer_id as
    ZERO_PRICE_OFFER_ID.\""""
    gifts = [
        offer for offer in state.offers.items
        if offer.status == pb.OFFER_STATUS_OPEN
        and offer.proposer_id == _PEER_STATION
        and offer.recipient_id == _OUR_STATION
        and Amounts.from_bundle(offer.receive) == _NOTHING
    ]
    # Zero gifts, or two we can't tell apart: either way, don't guess.
    _require(len(gifts) == 1, f"expected 1 open zero-price offer from P02, found {len(gifts)}")
    gift = gifts[0]
    _require_amounts("the gift's give", gift.give, _GIFT)
    _require(gift.offer_id, "the gift has an empty offer_id")
    return dataclasses.replace(position, zero_price_offer_id=gift.offer_id)


def _check_step_7_result(position: Position, result: pb.Result) -> Position:
    """"The result identifies the accepted offer and its transaction.\""""
    offer_id = _nullable_value(result.object_id)
    _require(
        offer_id == position.zero_price_offer_id,
        f"the result names offer {offer_id!r}, not the gift {position.zero_price_offer_id!r}",
    )
    _require(_nullable_value(result.transaction_id), "the result has no transaction_id")
    return position


def _check_step_8(position: Position, state: pb.State) -> Position:
    """"Your advertisement is absent from advertisements.items.\""""
    listed = [
        ad for ad in state.advertisements.items
        if ad.advertisement_id == position.advertisement_id
    ]
    _require(not listed, f"advertisement {position.advertisement_id!r} is still listed")
    return position


def _check_step_10(position: Position, state: pb.State) -> Position:
    """The final checks. Inventory and the two transactions are in _STEPS."""
    stored = len(state.request_results.items)
    _require(
        stored == _STORED_RESULTS,
        f"request_results has {stored} results, the spec says {_STORED_RESULTS}",
    )
    _require_amounts("imported_total", state.self.imported_total, _IMPORTED_TOTAL)
    _require_amounts("exported_total", state.self.exported_total, _EXPORTED_TOTAL)
    for name in _ZERO_BUNDLES:
        _require_amounts(f"self.{name}", getattr(state.self, name), _NOTHING)
    for name in _ZERO_COUNTS:
        count = getattr(state.self, name)
        _require(count == 0, f"self.{name} is {count}, the spec says 0")
    return position


# --- Each step's command -------------------------------------------------------
# Each takes the run ID, the request ID and the Position (for saved server IDs).


def _advertise_water_for_food(run_id: str, request_id: str, saved: Position):
    """Steps 2 and 9 send the same advertisement under different request IDs."""
    return commands.build_advertise(
        run_id, request_id, (pb.RESOURCE_WATER,), (pb.RESOURCE_FOOD,), _EXPIRES_TICK
    )


def _advertise_seeking_components(run_id: str, request_id: str, saved: Position):
    return commands.build_advertise(
        run_id, request_id, (), (pb.RESOURCE_COMPONENTS,), _EXPIRES_TICK
    )


def _offer_water_for_food(run_id: str, request_id: str, saved: Position):
    return commands.build_offer(
        run_id, request_id, _PEER_STATION, _OFFER_GIVE, _OFFER_RECEIVE, _EXPIRES_TICK
    )


def _accept_gift(run_id: str, request_id: str, saved: Position):
    return commands.build_accept(run_id, request_id, saved.zero_price_offer_id)


def _withdraw_advertisement(run_id: str, request_id: str, saved: Position):
    return commands.build_withdraw(run_id, request_id, saved.advertisement_id)


def _sync(run_id: str, request_id: None, saved: Position):
    return commands.build_sync(run_id)  # sync has no request ID


# --- The script ----------------------------------------------------------------


@dataclass(frozen=True)
class _Step:
    """One step of the spec's script.

    command:        builds the command the step sends; None if it only reads.
    request_id:     the spec's sample request ID for it (None for sync).
    replies:        the kinds of message the spec says arrive, in order.
    world_version, inventory, transactions: what the step's state must show
                    (None where the step has no state or the spec doesn't say).
    check_result, check_state: the step's own checks, if any.
    """

    command: Callable[[str, str | None, Position], pb.ClientMessage] | None
    request_id: str | None
    replies: tuple[str, ...]
    world_version: int | None
    inventory: Amounts | None
    transactions: int | None
    check_result: Callable[[Position, pb.Result], Position] | None
    check_state: Callable[[Position, pb.State], Position] | None


_STEPS = {
    1: _Step(None, None, ("state",), 2, _START, None, None, _check_step_1),
    2: _Step(
        _advertise_water_for_food, "student-advertise-1", ("result", "state"),
        3, _START, None, None, _check_step_2,
    ),
    3: _Step(
        _advertise_seeking_components, "student-advertise-seeking-1", ("result", "state"),
        4, _START, None, _check_step_3_result, _check_advertisement_seeking_components,
    ),
    4: _Step(
        _offer_water_for_food, "student-offer-1", ("result", "state"),
        5, _START, None, None, _check_step_4,
    ),
    # Steps 5 and 6: P02 acts on its own, so we only read.
    5: _Step(None, None, ("state",), 6, _AFTER_TRADE, 1, None, _check_step_5),
    6: _Step(None, None, ("state",), 7, _AFTER_TRADE, None, None, _check_step_6),
    7: _Step(
        _accept_gift, "student-accept-1", ("result", "state"),
        8, _AFTER_GIFT, 2, _check_step_7_result, _check_advertisement_seeking_components,
    ),
    8: _Step(
        _withdraw_advertisement, "student-withdraw-1", ("result", "state"),
        9, _AFTER_GIFT, 2, None, _check_step_8,
    ),
    # Step 9: "Only a protocol_error ... no result or automatic state".
    9: _Step(
        _advertise_water_for_food, "student-advertise-2", ("protocol_error",),
        None, None, None, None, None,
    ),
    10: _Step(_sync, None, ("state",), 9, _AFTER_GIFT, 2, None, _check_step_10),
}
