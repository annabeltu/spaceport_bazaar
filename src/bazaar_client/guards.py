"""
Checks every message before it's sent (contract by package A, body by package E).

This is the core of the safety plan. check() is the ONLY way to get bytes
that the connection will send, and Connection.send() only accepts bytes. So
no message can reach the server without passing every check here.

Package E's tests include one per gap listed in Part 2 of
tests/test_protobuf_safety_net.py: mistakes protobuf happily serializes, which
these checks must block.
"""
import re

from google.protobuf.descriptor import FieldDescriptor

from generated import bazaar_pb2 as pb

from bazaar_client.errors import GuardError
from bazaar_client.models import GuardContext

# The only protocol version this client speaks (check 3).
PROTOCOL_VERSION = "2.0"

# "Valid IDs contain 1-64 letters, digits, underscores, or hyphens" (check 5).
# Not anchored with ^/$ on purpose -- every call below uses .fullmatch(), which
# already requires the WHOLE string to match, so anchors would be redundant.
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")

# The spec's README writes runtime-only values as placeholders, like
# "<RUN_ID>". A real message must never still contain one (check 4).
_PLACEHOLDER_PATTERN = re.compile(r"<[A-Z_]+>")

# advertise, offer, accept and withdraw all trade real resources, so they wait
# for readiness (check 7). They're also exactly the four commands that carry a
# request_id (check 5, check 8) -- sync and ready don't have that field.
_TRADING_COMMANDS = frozenset({"advertise", "offer", "accept", "withdraw"})


def check(message: pb.ClientMessage, context: GuardContext) -> bytes:
    """Run every pre-send check on `message`, then return its bytes.

    Raises GuardError (from bazaar_client.errors) naming the first check that
    failed. When that happens nothing is sent, and the runner stops. Never
    changes `message`.

    The checks, in order:
    1. Exactly one command is selected.
    2. Every required field is set. The error lists the missing ones.
    3. `protocol_version` is "2.0".
    4. `run_id` equals context.run_id, and no string field still holds a spec
       placeholder such as "<RUN_ID>".
    5. Any `request_id` is 1-64 letters, digits, "_" or "-".
    6. The serialized size is at most context.max_command_bytes.
    7. Trading commands (advertise, offer, accept, withdraw) are blocked until
       context.is_ready. `sync` and `ready` are always allowed.
    8. A request_id that's already in context.sent_requests is only allowed
       for the byte-identical command. That makes it an exact retry; anything
       else would get REQUEST_ID_CONFLICT from the server.
    9. An offer's `give` never exceeds context.inventory for any resource.
    """
    which = _check_exactly_one_command(message)
    _check_required_fields(message)

    command = getattr(message, which)
    _check_protocol_version(command)
    _check_run_id(command, context)
    _check_no_placeholders(message)
    _check_request_id_format(which, command)

    # Every earlier check passed, so the message is guaranteed to serialize
    # cleanly. Do it once here and reuse the bytes for checks 6 and 8, and as
    # the return value -- no need to serialize the same message twice.
    data = message.SerializeToString()
    _check_size(data, context)
    _check_trading_readiness(which, context)
    _check_exact_retry(which, command, data, context)
    _check_offer_within_inventory(which, command, context)

    return data


# --- Check 1: exactly one command is selected ------------------------------------


def _check_exactly_one_command(message: pb.ClientMessage) -> str:
    """Return which command field is set, or raise if none is.

    protobuf's `oneof` never lets two fields be set at once, but it silently
    allows NONE to be set: an empty ClientMessage() serializes to zero bytes
    with no error at all. WhichOneof() returns None in that case, which is
    the gap this check closes.
    """
    which = message.WhichOneof("message")
    if which is None:
        raise GuardError(
            "no command selected: a ClientMessage must set exactly one of "
            "advertise, offer, accept, withdraw, sync or ready"
        )
    return which


# --- Check 2: every required field is set ----------------------------------------


def _check_required_fields(message: pb.ClientMessage) -> None:
    """Raise a GuardError listing every required field that's missing.

    protobuf itself refuses to serialize an incomplete message (that's
    Part 1 of tests/test_protobuf_safety_net.py), but it only raises a bare
    EncodeError with no detail. FindInitializationErrors() gives the exact
    dotted paths instead, like "offer.body.give.food", so the error here can
    say precisely what's missing.
    """
    missing = message.FindInitializationErrors()
    if missing:
        raise GuardError(f"missing required fields: {', '.join(missing)}")


# --- Check 3: protocol_version is "2.0" ------------------------------------------


def _check_protocol_version(command) -> None:
    """Raise if this command speaks a different protocol version."""
    if command.protocol_version != PROTOCOL_VERSION:
        raise GuardError(
            f"protocol_version: must be {PROTOCOL_VERSION!r}, "
            f"got {command.protocol_version!r}"
        )


# --- Check 4: run_id matches, and no placeholder is left --------------------------


def _check_run_id(command, context: GuardContext) -> None:
    """Raise if run_id isn't exactly the current run's ID.

    A blank run_id ("") is a perfectly valid protobuf string, so protobuf
    alone lets it through -- that's one of the Part 2 gaps.
    """
    if command.run_id != context.run_id:
        raise GuardError(
            f"run_id: must equal {context.run_id!r}, got {command.run_id!r}"
        )


def _check_no_placeholders(message: pb.ClientMessage) -> None:
    """Raise if any string field still holds a spec placeholder like "<RUN_ID>".

    Those placeholders are stand-ins the spec's README uses for values that
    only exist at runtime. One left in a real message means a builder forgot
    to fill it in -- and protobuf has no way to know that's wrong.
    """
    for path, value in _string_fields(message):
        if _PLACEHOLDER_PATTERN.search(value):
            raise GuardError(
                f"{'.'.join(path)}: still has a placeholder: {value!r}"
            )


def _string_fields(message, prefix=()):
    """Yield (path, value) for every string field set inside `message`.

    Walks into every sub-message that's present -- including each item of a
    repeated sub-message field -- so nested strings like
    offer.body.recipient_id are found too. No field in the current schema is
    a repeated string or a repeated sub-message reachable from a
    ClientMessage, but this handles both anyway: a future schema change
    (e.g. a repeated body) should never silently stop being scanned for
    placeholders. Mirrors required_field_paths() in
    tests/test_protobuf_safety_net.py, which walks the same tree for a
    different reason (finding required fields instead of string values).
    """
    for field in message.DESCRIPTOR.fields:
        path = (*prefix, field.name)
        value = getattr(message, field.name)
        if field.message_type is not None:
            if field.is_repeated:
                for item in value:
                    yield from _string_fields(item, path)
            elif message.HasField(field.name):
                yield from _string_fields(value, path)
        elif field.type == FieldDescriptor.TYPE_STRING:
            if field.is_repeated:
                yield from ((path, item) for item in value)
            else:
                yield path, value


# --- Check 5: request_id is 1-64 letters, digits, "_" or "-" ---------------------


def _check_request_id_format(which: str, command) -> None:
    """Raise if this command's request_id doesn't match the spec's format.

    Only the four trading commands have a request_id at all -- sync and
    ready don't carry one, so there's nothing to check for them.
    """
    if which not in _TRADING_COMMANDS:
        return
    request_id = command.request_id
    if not _REQUEST_ID_PATTERN.fullmatch(request_id):
        raise GuardError(
            "request_id: must be 1-64 letters, digits, '_' or '-', "
            f"got {request_id!r}"
        )


# --- Check 6: serialized size is at most context.max_command_bytes --------------


def _check_size(data: bytes, context: GuardContext) -> None:
    """Raise if the serialized message is bigger than the server allows.

    protobuf has no idea what limit the server enforces -- that number comes
    from the latest state's `rules.max_command_bytes`, not from the schema.
    """
    if len(data) > context.max_command_bytes:
        raise GuardError(
            f"message too large: {len(data)} bytes exceeds the limit of "
            f"{context.max_command_bytes}"
        )


# --- Check 7: trading commands wait for readiness --------------------------------


def _check_trading_readiness(which: str, context: GuardContext) -> None:
    """Raise if a trading command is sent before readiness is confirmed.

    `sync` and `ready` are always allowed. `ready` is how we BECOME ready in
    the first place, so requiring readiness for it would be a deadlock.
    """
    if which in _TRADING_COMMANDS and not context.is_ready:
        raise GuardError(
            f"not ready: {which} is blocked until the server confirms readiness"
        )


# --- Check 8: a reused request_id must be byte-identical (an exact retry) -------


def _check_exact_retry(which: str, command, data: bytes, context: GuardContext) -> None:
    """Raise if request_id was already sent with different bytes.

    Reusing a request_id is only safe when it's an exact retry -- the same
    command, resent because its result never arrived. Sending a DIFFERENT
    command under an old ID is exactly what the server's REQUEST_ID_CONFLICT
    exists to catch; this check catches it earlier, before anything is sent.
    """
    if which not in _TRADING_COMMANDS:
        return
    previous = context.sent_requests.get(command.request_id)
    if previous is not None and previous != data:
        raise GuardError(
            f"request_id reused: {command.request_id!r} was already sent "
            "with different bytes; only an exact retry may reuse an ID"
        )


# --- Check 9: an offer's give never exceeds the inventory ------------------------


def _check_offer_within_inventory(which: str, command, context: GuardContext) -> None:
    """Raise if an offer gives away more of a resource than we actually have.

    protobuf has no idea what's in our inventory -- offering
    18,446,744,073,709,551,615 water serializes just fine without this check.
    """
    if which != "offer":
        return
    give = command.body.give
    inventory = context.inventory
    for resource, offered, owned in (
        ("water", give.water, inventory.water),
        ("food", give.food, inventory.food),
        ("components", give.components, inventory.components),
    ):
        if offered > owned:
            raise GuardError(
                f"offer gives more {resource} than we have: {offered} > {owned}"
            )
