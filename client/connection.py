"""Binary Protobuf transport helpers."""
# Step numbers refer to starter/README.md, "Complete the exchange".
# Each label applies to the entire function below it.

import re

from google.protobuf import text_format
from generated import bazaar_pb2 as pb


# Shared by steps 1-10: decode and validate each binary server message.
def decode(payload):
    if not isinstance(payload, bytes):
        raise RuntimeError("Server sent text; binary Protobuf was expected.")
    message = pb.ServerMessage()
    message.ParseFromString(payload)
    if not message.IsInitialized() or message.WhichOneof("message") is None:
        raise RuntimeError("Server message is empty or missing required fields.")
    if getattr(message, message.WhichOneof("message")).protocol_version != "2.0":
        raise RuntimeError("Expected server protocol version 2.0.")
    return message


# Shared by steps 1-10: receive, print, and check the expected message type.
async def receive(websocket, expected=None):
    message = decode(await websocket.recv())
    print(text_format.MessageToString(message, as_utf8=True), flush=True)
    if expected and message.WhichOneof("message") != expected:
        raise RuntimeError(f"Expected {expected}, received: {message}")
    return message


# Steps 1-4 and 7-10: serialize and send commands; steps 5-6 only receive.
async def send(websocket, message):
    kind = message.WhichOneof("message")
    if kind is None or not message.IsInitialized():
        raise RuntimeError("Command is empty or missing required fields.")
    command = getattr(message, kind)
    if command.protocol_version != "2.0":
        raise RuntimeError("Expected command protocol version 2.0.")
    if not command.run_id or "<" in command.run_id:
        raise RuntimeError("Use the run ID from the server, not a placeholder.")
    # Ready and sync have no request ID; other commands need a valid label.
    if kind not in ("ready", "sync") and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", command.request_id):
        raise RuntimeError("Request ID must be 1-64 letters, digits, underscores, or hyphens.")
    payload = message.SerializeToString()
    if len(payload) > 16_384:
        raise RuntimeError("Command exceeds the 16,384-byte limit.")
    await websocket.send(payload)
