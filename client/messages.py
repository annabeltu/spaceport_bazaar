"""Construct protocol commands without performing network I/O."""
from generated import bazaar_pb2 as pb


def _build(kind, message_type, run_id, request_id=None):
    message = pb.ClientMessage()
    command = getattr(message, kind)
    command.type = message_type
    command.protocol_version = "2.0"
    command.run_id = run_id
    if request_id is not None:
        command.request_id = request_id
    return message, command


def build_ready(run_id, snapshot_sequence):
    message, command = _build("ready", pb.READY_TYPE_READY, run_id)
    command.ready = True
    command.snapshot_sequence = snapshot_sequence
    return message


def build_advertisement(run_id, request_id, selling, seeking, expires_tick=6):
    message, command = _build("advertise", pb.ADVERTISE_TYPE_ADVERTISE, run_id, request_id)
    command.body.selling.SetInParent()
    command.body.selling.items.extend(selling)
    command.body.seeking.SetInParent()
    command.body.seeking.items.extend(seeking)
    command.body.expires_tick = expires_tick
    return message


def build_offer(run_id, request_id="student-offer-1"):
    message, command = _build("offer", pb.OFFER_COMMAND_TYPE_OFFER, run_id, request_id)
    command.body.recipient_id = "P02"
    command.body.give.CopyFrom(pb.Bundle(water=2, food=0, components=0))
    command.body.receive.CopyFrom(pb.Bundle(water=0, food=1, components=0))
    command.body.expires_tick = 6
    return message


def build_accept(run_id, offer_id, request_id="student-accept-1"):
    message, command = _build("accept", pb.ACCEPT_TYPE_ACCEPT, run_id, request_id)
    command.body.offer_id = offer_id
    return message


def build_withdraw(run_id, object_id, request_id="student-withdraw-1"):
    message, command = _build("withdraw", pb.WITHDRAW_TYPE_WITHDRAW, run_id, request_id)
    command.body.object_id = object_id
    return message


def build_sync(run_id):
    message, _ = _build("sync", pb.SYNC_TYPE_SYNC, run_id)
    return message
