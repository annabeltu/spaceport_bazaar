"""Binary Protobuf transport helpers."""
# Step numbers refer to starter/README.md, "Complete the exchange".
# Each label applies to the entire function below it.

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
    await websocket.send(message.SerializeToString())
