"""Pure state machine for the practice exchange; this module opens no sockets."""

from __future__ import annotations

from dataclasses import dataclass, replace

from generated import bazaar_pb2 as pb

from fake_server.messages import (
    PROTOCOL_VERSION,
    build_initial_state,
    build_protocol_error,
    build_readiness,
    build_result,
    build_state,
)


@dataclass(frozen=True)
class Scenario:
    """Immutable progress through the scripted practice exchange."""

    run_id: str
    ready: bool
    world_version: int
    snapshot_sequence: int
    next_step: int
    stored_results: tuple[bytes, ...]
    request_records: tuple[tuple[str, bytes, bytes], ...]

    @classmethod
    def create(cls, run_id: str) -> Scenario:
        return cls(
            run_id=run_id,
            ready=False,
            world_version=2,
            snapshot_sequence=1,
            next_step=1,
            stored_results=(),
            request_records=(),
        )

    def initial_message(self) -> pb.ServerMessage:
        return self._state()

    def on_new_connection(self) -> Scenario:
        """Keep world progress while resetting connection-local readiness and sequence."""
        return replace(self, ready=False, snapshot_sequence=1)

    def handle(
        self, message: pb.ClientMessage
    ) -> tuple[Scenario, tuple[pb.ServerMessage, ...]]:
        """Return new scenario state and replies for one complete client command."""
        command_name = message.WhichOneof("message")
        if command_name is None:
            return self, (self._bad_message(),)
        command = getattr(message, command_name)

        if command.protocol_version != PROTOCOL_VERSION:
            error = build_protocol_error(
                self.run_id,
                getattr(command, "request_id", None),
                pb.CONTROL_CODE_UNSUPPORTED_VERSION,
                True,
            )
            return self, (error,)
        if command.run_id != self.run_id:
            error = build_protocol_error(
                self.run_id,
                getattr(command, "request_id", None),
                pb.CONTROL_CODE_RUN_MISMATCH,
                True,
            )
            return self, (error,)

        if command_name == "sync":
            sequence = self.snapshot_sequence + 1
            updated = replace(self, snapshot_sequence=sequence)
            return updated, (updated._state(),)

        if command_name == "ready":
            updated = replace(self, ready=command.ready, next_step=max(self.next_step, 2))
            reply = build_readiness(
                self.run_id, command.ready, command.snapshot_sequence
            )
            return updated, (reply,)

        if not self.ready:
            return self, (self._bad_message(getattr(command, "request_id", None)),)

        request_id = getattr(command, "request_id", None)
        if request_id is not None:
            retry = self._retry(request_id, message.SerializeToString())
            if retry is not None:
                return retry

        handlers = {
            2: self._step_2,
            3: self._step_3,
            4: self._step_4,
            7: self._step_7,
            8: self._step_8,
            9: self._step_9,
        }
        handler = handlers.get(self.next_step)
        if handler is None:
            raise ValueError("scenario mismatch")
        return handler(command_name, command, message.SerializeToString())

    def _state(self) -> pb.ServerMessage:
        return build_state(
            self.run_id,
            self.snapshot_sequence,
            self.world_version,
            self.stored_results,
        )

    def _bad_message(self, request_id: str | None = None) -> pb.ServerMessage:
        return build_protocol_error(
            self.run_id,
            request_id,
            pb.CONTROL_CODE_BAD_MESSAGE,
            False,
        )

    def _success(
        self,
        request_id: str,
        world_version: int,
        next_step: int,
        *,
        object_id: str | None = None,
        transaction_id: str | None = None,
        request_bytes: bytes,
    ) -> tuple[Scenario, pb.ServerMessage]:
        result = build_result(
            self.run_id,
            request_id,
            world_version,
            object_id=object_id,
            transaction_id=transaction_id,
        )
        stored = self.stored_results + (result.result.SerializeToString(),)
        records = self.request_records + (
            (request_id, request_bytes, result.result.SerializeToString()),
        )
        updated = replace(
            self,
            world_version=world_version,
            snapshot_sequence=self.snapshot_sequence + 1,
            next_step=next_step,
            stored_results=stored,
            request_records=records,
        )
        return updated, result

    def _retry(
        self, request_id: str, request_bytes: bytes
    ) -> tuple[Scenario, tuple[pb.ServerMessage, ...]] | None:
        for saved_id, saved_request, saved_result in self.request_records:
            if saved_id != request_id:
                continue
            if saved_request == request_bytes:
                result = pb.ServerMessage()
                result.result.ParseFromString(saved_result)
            else:
                result = build_result(
                    self.run_id,
                    request_id,
                    self.world_version,
                    ok=False,
                    code=pb.RESULT_CODE_REQUEST_ID_CONFLICT,
                )
            # UNVERIFIED: the spec promises a conflict result but doesn't say
            # whether a state follows it. Package K checks this extra state.
            updated = replace(self, snapshot_sequence=self.snapshot_sequence + 1)
            return updated, (result, updated._state())
        return None

    def _step_2(self, name: str, command, request_bytes: bytes):
        if not _matches_advertise(
            name,
            command,
            request_id="student-advertise-1",
            selling=(pb.RESOURCE_WATER,),
            seeking=(pb.RESOURCE_FOOD,),
        ):
            raise ValueError("scenario mismatch")
        updated, result = self._success(
            command.request_id,
            3,
            3,
            object_id="fake-p01-advertisement-1",
            request_bytes=request_bytes,
        )
        return updated, (result, updated._state())

    def _step_3(self, name: str, command, request_bytes: bytes):
        if not _matches_advertise(
            name,
            command,
            request_id="student-advertise-seeking-1",
            selling=(),
            seeking=(pb.RESOURCE_COMPONENTS,),
        ):
            raise ValueError("scenario mismatch")
        updated, result = self._success(
            command.request_id,
            4,
            4,
            object_id="fake-p01-advertisement-2",
            request_bytes=request_bytes,
        )
        return updated, (result, updated._state())

    def _step_4(self, name: str, command, request_bytes: bytes):
        if not _matches_offer(name, command):
            raise ValueError("scenario mismatch")
        updated, result = self._success(
            command.request_id,
            5,
            7,
            object_id="fake-p01-offer-1",
            request_bytes=request_bytes,
        )
        state_5 = updated._state()
        updated = replace(
            updated,
            world_version=6,
            snapshot_sequence=updated.snapshot_sequence + 1,
        )
        state_6 = updated._state()
        updated = replace(
            updated,
            world_version=7,
            snapshot_sequence=updated.snapshot_sequence + 1,
        )
        return updated, (result, state_5, state_6, updated._state())

    def _step_7(self, name: str, command, request_bytes: bytes):
        if (
            name != "accept"
            or command.request_id != "student-accept-1"
            or command.body.offer_id != "fake-p02-gift-1"
        ):
            raise ValueError("scenario mismatch")
        updated, result = self._success(
            command.request_id,
            8,
            8,
            object_id="fake-p02-gift-1",
            transaction_id="fake-transaction-2",
            request_bytes=request_bytes,
        )
        return updated, (result, updated._state())

    def _step_8(self, name: str, command, request_bytes: bytes):
        if (
            name != "withdraw"
            or command.request_id != "student-withdraw-1"
            or command.body.object_id != "fake-p01-advertisement-2"
        ):
            raise ValueError("scenario mismatch")
        updated, result = self._success(
            command.request_id,
            9,
            9,
            object_id=command.body.object_id,
            request_bytes=request_bytes,
        )
        return updated, (result, updated._state())

    def _step_9(self, name: str, command, request_bytes: bytes):
        if not _matches_advertise(
            name,
            command,
            request_id="student-advertise-2",
            selling=(pb.RESOURCE_WATER,),
            seeking=(pb.RESOURCE_FOOD,),
        ):
            raise ValueError("scenario mismatch")
        error = build_protocol_error(
            self.run_id,
            command.request_id,
            pb.CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED,
            False,
        )
        return replace(self, next_step=10), (error,)


def _matches_advertise(
    name: str,
    command,
    *,
    request_id: str,
    selling: tuple[int, ...],
    seeking: tuple[int, ...],
) -> bool:
    return (
        name == "advertise"
        and command.request_id == request_id
        and tuple(command.body.selling.items) == selling
        and tuple(command.body.seeking.items) == seeking
        and command.body.expires_tick == 6
    )


def _matches_offer(name: str, command) -> bool:
    return (
        name == "offer"
        and command.request_id == "student-offer-1"
        and command.body.recipient_id == "P02"
        and _bundle_tuple(command.body.give) == (2, 0, 0)
        and _bundle_tuple(command.body.receive) == (0, 1, 0)
        and command.body.expires_tick == 6
    )


def _bundle_tuple(bundle: pb.Bundle) -> tuple[int, int, int]:
    return bundle.water, bundle.food, bundle.components
