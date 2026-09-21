"""
Shared data types (package A). Frozen once package A merges.

Every class here is a FROZEN dataclass: once one is created, its fields can't
be reassigned. To "change" one, build a new one, for example with
`dataclasses.replace(client, is_ready=True)`. Then a value you're holding can
never change underneath you because some other code touched it.

Frozen stops fields from being REASSIGNED, but it can't stop a field's value
from being changed in place. Two kinds of value can be, so every package
follows these two rules:

- Mapping fields (like ClientState.results): pass a new read-only mapping,
  `types.MappingProxyType(new_dict)`, built from a dict nobody else holds. Don't
  keep the dict after wrapping it: the proxy shows any change made to it.
- Protobuf message fields (like ClientState.snapshot): protobuf messages can
  always be changed in place. Store a COPY and never change it afterwards:
      copy = pb.State()
      copy.CopyFrom(original)
"""
from collections.abc import Mapping
from dataclasses import dataclass

from generated import bazaar_pb2 as pb

# The largest number a protobuf uint64 field can hold. The spec says to
# "preserve full 64-bit integers", so an amount can be this big, but no bigger.
MAX_UINT64 = 2**64 - 1


def _check_amount(name, value):
    """Raise a clear error if `value` can't be a resource amount."""
    # In Python, True and False count as the numbers 1 and 0. An amount of
    # True is almost certainly a bug, so rule bool out explicitly.
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be a whole number, got {value!r}")
    if not 0 <= value <= MAX_UINT64:
        raise ValueError(f"{name} must be between 0 and 2**64 - 1, got {value}")


@dataclass(frozen=True)
class Amounts:
    """A (water, food, components) bundle: the spec's `Bundle`, as plain numbers.

    There are no defaults on purpose. The spec requires all three amounts,
    zeros included, and a forgotten zero is the classic mistake. So
    `Amounts(water=2)` is a TypeError; write
    `Amounts(water=2, food=0, components=0)`.
    """

    water: int
    food: int
    components: int

    def __post_init__(self):
        # dataclasses call __post_init__ right after creating the object, so a
        # bad amount fails HERE, with a clear message, instead of later when
        # it's turned into protobuf.
        _check_amount("water", self.water)
        _check_amount("food", self.food)
        _check_amount("components", self.components)

    # "Amounts" is in quotes because the class isn't finished being defined
    # yet when Python reads this line.
    @classmethod
    def from_bundle(cls, bundle: pb.Bundle) -> "Amounts":
        """The amounts in a protobuf Bundle, e.g. `state.self.inventory`."""
        return cls(water=bundle.water, food=bundle.food, components=bundle.components)

    def to_bundle(self) -> pb.Bundle:
        """A NEW protobuf Bundle with all three amounts set, zeros included."""
        return pb.Bundle(water=self.water, food=self.food, components=self.components)


@dataclass(frozen=True)
class GuardContext:
    """Everything guards.check() needs to know before a message can be sent.

    Built by state.guard_context(client), so the guards never have to dig
    through the whole ClientState.

    run_id:            the current run. Every command must carry exactly this.
    is_ready:          readiness is confirmed on THIS connection. Trading
                       commands (advertise, offer, accept, withdraw) wait for it.
    max_command_bytes: the server's size limit, from the latest state's
                       `rules`, not a hardcoded copy.
    inventory:         our station's inventory in the latest state.
    sent_requests:     request_id -> the exact bytes already sent with that ID.
                       Reusing an ID is only allowed for the same bytes (an
                       exact retry). Read-only mapping, see the module docstring.
    """

    run_id: str
    is_ready: bool
    max_command_bytes: int
    inventory: Amounts
    sent_requests: Mapping[str, bytes]


@dataclass(frozen=True)
class ClientState:
    """The client's whole view of the world at one moment.

    Only state.py builds these, and each of its functions returns a NEW one.
    Start from state.initial().

    run_id:        the current run, copied from the first state. None until
                   then.
    snapshot:      the latest `state` message, stored as a copy. Each new state
                   REPLACES it whole: its inventory already includes every
                   completed trade, so nothing is ever added to it. None until
                   the first state arrives.
    is_ready:      the server confirmed our readiness on THIS connection.
                   Every new connection starts at False.
    results:       request_id -> the `result` the server sent for it (copies).
                   Read-only mapping.
    sent_requests: request_id -> the exact bytes we sent with that ID.
                   Read-only mapping.
    """

    run_id: str | None
    snapshot: pb.State | None
    is_ready: bool
    results: Mapping[str, pb.Result]
    sent_requests: Mapping[str, bytes]
