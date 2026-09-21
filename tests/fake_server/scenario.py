"""Pure state machine for the practice exchange; this module opens no sockets."""

from __future__ import annotations

from dataclasses import dataclass, replace

from generated import bazaar_pb2 as pb

from fake_server.messages import build_initial_state, build_readiness


@dataclass(frozen=True)
class Scenario:
    """Immutable progress through the scripted practice exchange."""

    run_id: str
    ready: bool
    world_version: int
    snapshot_sequence: int

    @classmethod
    def create(cls, run_id: str) -> Scenario:
        return cls(
            run_id=run_id,
            ready=False,
            world_version=2,
            snapshot_sequence=1,
        )

    def initial_message(self) -> pb.ServerMessage:
        return build_initial_state(self.run_id, self.snapshot_sequence)

    def handle(
        self, message: pb.ClientMessage
    ) -> tuple[Scenario, tuple[pb.ServerMessage, ...]]:
        """Return new scenario state and replies for one complete client command."""
        if message.WhichOneof("message") != "ready":
            raise ValueError("expected readiness declaration")

        command = message.ready
        if command.run_id != self.run_id:
            raise ValueError("run ID does not match this scenario")

        updated = replace(self, ready=command.ready)
        reply = build_readiness(
            run_id=self.run_id,
            ready=command.ready,
            snapshot_sequence=command.snapshot_sequence,
        )
        return updated, (reply,)
