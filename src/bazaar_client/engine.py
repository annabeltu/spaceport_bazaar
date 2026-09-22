"""
Decides the client's next move (contract and decision types by package A,
Position's fields and the functions by package H).

The engine is pure logic: no network, no clock, no printing. Given what just
arrived, it returns where we are now and what to do next. That makes every
case a fast unit test.

It's scripted for the spec's 10-step practice exercise on purpose. The
classroom game needs a real strategy, which gets its own plan once the rules
are published.
"""
from dataclasses import dataclass

from generated import bazaar_pb2 as pb

from bazaar_client.models import ClientState


@dataclass(frozen=True)
class Position:
    """Where we are in the practice script.

    Package H picks the fields, for example the current step and the IDs
    saved so far (ADVERTISEMENT_ID, ZERO_PRICE_OFFER_ID). Everyone else treats
    a Position as opaque: the runner just keeps the latest one decide()
    returns and passes it back in with the next message.
    """


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
    raise NotImplementedError("package H")


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
    raise NotImplementedError("package H")
