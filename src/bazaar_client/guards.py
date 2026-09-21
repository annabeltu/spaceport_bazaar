"""
Checks every message before it's sent (contract by package A, body by package E).

This is the core of the safety plan. check() is the ONLY way to get bytes
that the connection will send, and Connection.send() only accepts bytes. So
no message can reach the server without passing every check here.

Package E's tests include one per gap listed in Part 2 of
tests/test_protobuf_safety_net.py: mistakes protobuf happily serializes, which
these checks must block.
"""
from generated import bazaar_pb2 as pb

from bazaar_client.models import GuardContext


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
    raise NotImplementedError("package E")
