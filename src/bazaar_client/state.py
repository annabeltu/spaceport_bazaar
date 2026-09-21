"""
The client's view of the world (contract by package A, body by package F).

Every function takes a ClientState and returns a NEW one. None of them ever
changes the ClientState it was given, or any protobuf message it was given.
That makes each step easy to test: same input, same output, nothing hidden.
See models.py for how to store mappings and protobuf messages safely.
"""
from generated import bazaar_pb2 as pb

from bazaar_client.models import ClientState, GuardContext


def initial() -> ClientState:
    """The state before anything has arrived: no run, no snapshot, not ready,
    and no results or sent requests."""
    raise NotImplementedError("package F")


def apply_server_message(client: ClientState, message: pb.ServerMessage) -> ClientState:
    """`client` updated with one message from the server.

    - `state`: stored as a COPY, and it REPLACES the old snapshot whole. Its
      inventory already includes every completed trade, so nothing is added
      again. Example: after step 5 the inventory is (28,31,30), not
      double-counted.
      A state whose run_id differs from client.run_id means the server
      restarted with a new run: the old results and sent requests are thrown
      away, because their IDs mean nothing in the new run.
    - `result`: a copy is stored in `results` under its request_id.
    - `readiness`: sets is_ready only if `ready` is true AND its run_id and
      snapshot_sequence match the current snapshot (the ones our `ready`
      declared). A mismatch leaves is_ready unchanged.
    - `protocol_error`: changes nothing. The engine decides what it means, and
      the runner follows its close_session.
    """
    raise NotImplementedError("package F")


def record_sent(client: ClientState, request_id: str, data: bytes) -> ClientState:
    """`client` with `data` recorded in sent_requests under `request_id`.

    The runner calls this right after sending a command that has a request_id
    (advertise, offer, accept, withdraw). `ready` and `sync` have no request_id,
    so they're never recorded. The guards use sent_requests to allow exact
    retries and block a changed command under an old ID.
    """
    raise NotImplementedError("package F")


def on_new_connection(client: ClientState) -> ClientState:
    """`client` after the runner opens a new connection.

    Clears is_ready, because readiness is required on EVERY connection. Keeps
    everything else: the same running server keeps the run's progress, and
    the new connection's first state (snapshot_sequence 1) replaces the
    snapshot when it arrives.
    """
    raise NotImplementedError("package F")


def guard_context(client: ClientState) -> GuardContext:
    """What guards.check() needs, taken from `client`.

    max_command_bytes and inventory come from the latest snapshot
    (snapshot.rules.max_command_bytes and snapshot.self.inventory). Raises
    GuardError (from bazaar_client.errors) if no state has arrived yet: without
    a snapshot there's nothing to check a message against, so nothing is sent.
    """
    raise NotImplementedError("package F")
