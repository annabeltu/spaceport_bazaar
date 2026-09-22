"""
The client's view of the world (contract by package A, body by package F).

Every function takes a ClientState and returns a NEW one. None of them ever
changes the ClientState it was given, or any protobuf message it was given.
That makes each step easy to test: same input, same output, nothing hidden.
See models.py for how to store mappings and protobuf messages safely.
"""
import dataclasses
from types import MappingProxyType

from generated import bazaar_pb2 as pb

from bazaar_client.errors import GuardError
from bazaar_client.models import Amounts, ClientState, GuardContext


def initial() -> ClientState:
    """The state before anything has arrived: no run, no snapshot, not ready,
    no `ready` sent yet, and no results or sent requests."""
    return ClientState(
        run_id=None,
        snapshot=None,
        is_ready=False,
        sent_ready_sequence=None,
        # Empty dicts wrapped in MappingProxyType: read-only from the start,
        # per the rule in models.py's docstring.
        results=MappingProxyType({}),
        sent_requests=MappingProxyType({}),
    )


def apply_server_message(client: ClientState, message: pb.ServerMessage) -> ClientState:
    """`client` updated with one message from the server.

    - `state`: stored as a COPY, and it REPLACES the old snapshot whole. Its
      inventory already includes every completed trade, so nothing is added
      again. Example: after step 5 the inventory is (28,31,30), not
      double-counted.
      A state whose run_id differs from client.run_id means the server
      restarted with a new run: the old results and sent requests are thrown
      away, because their IDs mean nothing in the new run. is_ready and
      sent_ready_sequence are reset too (False and None): readiness was never
      declared for the new run.
    - `result`: a copy is stored in `results` under its request_id.
    - `readiness`: sets is_ready only if `ready` is true, its run_id equals
      client.run_id, AND its snapshot_sequence equals
      client.sent_ready_sequence: the number our `ready` declared, not the
      latest state's (another state may have arrived since). If we haven't
      sent a `ready` (None), or anything differs, is_ready is unchanged.
    - `protocol_error`: changes nothing. The engine decides what it means, and
      the runner follows its close_session.
    """
    # WhichOneof tells us which of the four server message kinds this is,
    # e.g. "state" or "readiness" -- exactly the field names used below.
    kind = message.WhichOneof("message")
    if kind == "state":
        return _apply_state(client, message.state)
    if kind == "result":
        return _apply_result(client, message.result)
    if kind == "readiness":
        return _apply_readiness(client, message.readiness)
    # "protocol_error" (or, defensively, nothing selected at all): the spec
    # leaves handling protocol_error to the engine, so state.py does nothing.
    return client


def _apply_state(client: ClientState, incoming: pb.State) -> ClientState:
    """Handle one `state` message: see apply_server_message's docstring."""
    snapshot_copy = pb.State()
    snapshot_copy.CopyFrom(incoming)  # a copy, so mutating `incoming` later is safe

    # `client.run_id is None` (the very first state) also takes this branch,
    # since None never equals a run_id string. That's correct: a fresh client
    # already has empty results/sent_requests and is_ready False, so
    # "resetting" them here is a no-op, and we still need to adopt the run_id.
    if incoming.run_id != client.run_id:
        return dataclasses.replace(
            client,
            run_id=incoming.run_id,
            snapshot=snapshot_copy,
            is_ready=False,
            sent_ready_sequence=None,
            results=MappingProxyType({}),
            sent_requests=MappingProxyType({}),
        )

    return dataclasses.replace(client, snapshot=snapshot_copy)


def _apply_result(client: ClientState, incoming: pb.Result) -> ClientState:
    """Handle one `result` message: store a copy under its request_id."""
    result_copy = pb.Result()
    result_copy.CopyFrom(incoming)

    updated_results = dict(client.results)  # a plain, mutable copy to build from
    updated_results[incoming.request_id] = result_copy
    return dataclasses.replace(client, results=MappingProxyType(updated_results))


def _apply_readiness(client: ClientState, incoming: pb.Readiness) -> ClientState:
    """Handle one `readiness` message: confirm is_ready only on a full match."""
    matches = (
        incoming.ready
        and incoming.run_id == client.run_id
        and incoming.snapshot_sequence == client.sent_ready_sequence
    )
    if not matches:
        return client
    return dataclasses.replace(client, is_ready=True)


def record_sent(client: ClientState, request_id: str, data: bytes) -> ClientState:
    """`client` with `data` recorded in sent_requests under `request_id`.

    The runner calls this right after sending a command that has a request_id
    (advertise, offer, accept, withdraw). `ready` and `sync` have no request_id,
    so they're never recorded. The guards use sent_requests to allow exact
    retries and block a changed command under an old ID.
    """
    updated_sent = dict(client.sent_requests)
    updated_sent[request_id] = data
    return dataclasses.replace(client, sent_requests=MappingProxyType(updated_sent))


def record_ready(client: ClientState, snapshot_sequence: int) -> ClientState:
    """`client` with `snapshot_sequence` saved as sent_ready_sequence.

    The runner calls this right after sending a `ready` command, passing the
    number that command carried (`message.ready.snapshot_sequence`). The
    server's `readiness` reply is then checked against it: the spec says the
    reply must "match your message". This doesn't set is_ready; only that
    reply can.
    """
    return dataclasses.replace(client, sent_ready_sequence=snapshot_sequence)


def on_new_connection(client: ClientState) -> ClientState:
    """`client` after the runner opens a new connection.

    Clears is_ready and sent_ready_sequence, because readiness is required on
    EVERY connection: a `ready` sent on the old connection doesn't count on
    the new one. Keeps everything else: the same running server keeps the
    run's progress, and the new connection's first state (snapshot_sequence 1)
    replaces the snapshot when it arrives.
    """
    return dataclasses.replace(client, is_ready=False, sent_ready_sequence=None)


def guard_context(client: ClientState) -> GuardContext:
    """What guards.check() needs, taken from `client`.

    max_command_bytes and inventory come from the latest snapshot
    (snapshot.rules.max_command_bytes and snapshot.self.inventory). Raises
    GuardError (from bazaar_client.errors) if no state has arrived yet: without
    a snapshot there's nothing to check a message against, so nothing is sent.
    """
    if client.snapshot is None:
        raise GuardError("guard_context: no state has arrived yet, so nothing can be checked")

    return GuardContext(
        run_id=client.run_id,
        is_ready=client.is_ready,
        max_command_bytes=client.snapshot.rules.max_command_bytes,
        inventory=Amounts.from_bundle(client.snapshot.self.inventory),
        # Already a read-only mapping that nothing else holds a mutable
        # reference to (record_sent discards its working dict after wrapping
        # it), so it's safe to share as-is instead of copying again.
        sent_requests=client.sent_requests,
    )
