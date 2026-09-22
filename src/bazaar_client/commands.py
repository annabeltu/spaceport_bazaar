"""
Builders for the six client commands (contract by package A, body by package D).

Each builder returns a NEW pb.ClientMessage, so changing one message can never
affect another. The builder sets `protocol_version: "2.0"` and the command's
`type` itself, so a caller can't forget them. Callers pass only what differs
between messages.

IDs always come from the caller. A builder never makes up a run_id,
request_id or object ID: run IDs and object IDs come from the server, and
request IDs are chosen by the engine.

The builders check nothing beyond what protobuf checks. That's
guards.check()'s job, and guards.check() is the only way to get bytes to send.

Package D's test: for each of the 8 answer keys in tests/fixtures/spec_messages/,
the matching builder, given the same placeholder values, produces exactly the
same bytes.
"""
from generated import bazaar_pb2 as pb

from bazaar_client.models import Amounts


def build_ready(run_id: str, snapshot_sequence: int, ready: bool) -> pb.ClientMessage:
    """A `ready` command: step 1 (answer key 01_ready.textproto).

    `snapshot_sequence` is the sequence number of the state we just read on
    THIS connection (1 on a new connection). `ready=False` is allowed and
    still sets the field: in proto2, a required `false` must be present.
    """
    raise NotImplementedError("package D")


def build_advertise(
    run_id: str,
    request_id: str,
    selling: tuple[int, ...],
    seeking: tuple[int, ...],
    expires_tick: int,
) -> pb.ClientMessage:
    """An `advertise` command: steps 2, 3 and 9.

    `selling` and `seeking` are pb.Resource values, e.g.
    `(pb.RESOURCE_WATER,)`. An empty tuple must still produce the list's
    container: step 3 sends `selling {}`, and leaving `selling` out is invalid.
    In proto2 a sub-message you never touch counts as missing, so call
    `SetInParent()` on it to mark it present.
    """
    raise NotImplementedError("package D")


def build_offer(
    run_id: str,
    request_id: str,
    recipient_id: str,
    give: Amounts,
    receive: Amounts,
    expires_tick: int,
) -> pb.ClientMessage:
    """An `offer` command: step 4.

    Amounts are from the proposer's (our) side: `give` is what we pay and
    `receive` is what we ask for. Every amount is sent, zeros included.
    """
    raise NotImplementedError("package D")


def build_accept(run_id: str, request_id: str, offer_id: str) -> pb.ClientMessage:
    """An `accept` command: step 7. `offer_id` comes from a state's offers."""
    raise NotImplementedError("package D")


def build_withdraw(run_id: str, request_id: str, object_id: str) -> pb.ClientMessage:
    """A `withdraw` command: step 8.

    `object_id` is the server's ID for our advertisement: step 3's
    `result.object_id.value` (the spec's ADVERTISEMENT_ID).
    """
    raise NotImplementedError("package D")


def build_sync(run_id: str) -> pb.ClientMessage:
    """A `sync` command: step 10. It asks for a fresh state.

    Sync has no body and no request_id. It never uses up a stored-result slot,
    and it's allowed even before readiness.
    """
    raise NotImplementedError("package D")
