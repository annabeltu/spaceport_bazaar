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

# The spec pins every command to this protocol version. A constant instead of
# a literal repeated six times means there's exactly one place to change it.
PROTOCOL_VERSION = "2.0"


def build_ready(run_id: str, snapshot_sequence: int, ready: bool) -> pb.ClientMessage:
    """A `ready` command: step 1 (answer key 01_ready.textproto).

    `snapshot_sequence` is the sequence number of the state we just read on
    THIS connection (1 on a new connection). `ready=False` is allowed and
    still sets the field: in proto2, a required `false` must be present.
    """
    message = pb.ClientMessage()
    ready_command = message.ready
    ready_command.type = pb.READY_TYPE_READY
    ready_command.protocol_version = PROTOCOL_VERSION
    ready_command.run_id = run_id
    # Setting `ready = False` still touches the field (unlike leaving it
    # alone), which is what makes a required `false` present on the wire.
    ready_command.ready = ready
    ready_command.snapshot_sequence = snapshot_sequence
    return message


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
    message = pb.ClientMessage()
    command = message.advertise
    command.type = pb.ADVERTISE_TYPE_ADVERTISE
    command.protocol_version = PROTOCOL_VERSION
    command.run_id = run_id
    command.request_id = request_id

    # SetInParent() marks a required sub-message as present even if we add no
    # items to its list, which is what step 3's `selling {}` needs.
    command.body.selling.SetInParent()
    command.body.selling.items.extend(selling)
    command.body.seeking.SetInParent()
    command.body.seeking.items.extend(seeking)
    command.body.expires_tick = expires_tick
    return message


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
    message = pb.ClientMessage()
    command = message.offer
    command.type = pb.OFFER_COMMAND_TYPE_OFFER
    command.protocol_version = PROTOCOL_VERSION
    command.run_id = run_id
    command.request_id = request_id
    command.body.recipient_id = recipient_id
    # to_bundle() already sets water/food/components explicitly, zeros
    # included, so CopyFrom carries all three over -- never just the ones
    # that happen to be non-zero.
    command.body.give.CopyFrom(give.to_bundle())
    command.body.receive.CopyFrom(receive.to_bundle())
    command.body.expires_tick = expires_tick
    return message


def build_accept(run_id: str, request_id: str, offer_id: str) -> pb.ClientMessage:
    """An `accept` command: step 7. `offer_id` comes from a state's offers."""
    message = pb.ClientMessage()
    command = message.accept
    command.type = pb.ACCEPT_TYPE_ACCEPT
    command.protocol_version = PROTOCOL_VERSION
    command.run_id = run_id
    command.request_id = request_id
    command.body.offer_id = offer_id
    return message


def build_withdraw(run_id: str, request_id: str, object_id: str) -> pb.ClientMessage:
    """A `withdraw` command: step 8.

    `object_id` is the server's ID for our advertisement: step 3's
    `result.object_id.value` (the spec's ADVERTISEMENT_ID).
    """
    message = pb.ClientMessage()
    command = message.withdraw
    command.type = pb.WITHDRAW_TYPE_WITHDRAW
    command.protocol_version = PROTOCOL_VERSION
    command.run_id = run_id
    command.request_id = request_id
    command.body.object_id = object_id
    return message


def build_sync(run_id: str) -> pb.ClientMessage:
    """A `sync` command: step 10. It asks for a fresh state.

    Sync has no body and no request_id. It never uses up a stored-result slot,
    and it's allowed even before readiness.
    """
    message = pb.ClientMessage()
    command = message.sync
    command.type = pb.SYNC_TYPE_SYNC
    command.protocol_version = PROTOCOL_VERSION
    command.run_id = run_id
    return message
