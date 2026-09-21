"""Binary Protobuf transport helpers."""
from google.protobuf import text_format
from generated import bazaar_pb2 as pb


def decode(payload):
    if not isinstance(payload, bytes):
        raise RuntimeError("Server sent text; binary Protobuf was expected.")
    message = pb.ServerMessage()
    message.ParseFromString(payload)
    if not message.IsInitialized() or message.WhichOneof("message") is None:
        raise RuntimeError("Server message is empty or missing required fields.")
    return message


async def receive(websocket, expected=None):
    message = decode(await websocket.recv())
    print(text_format.MessageToString(message, as_utf8=True), flush=True)
    if expected and message.WhichOneof("message") != expected:
        raise RuntimeError(f"Expected {expected}, received: {message}")
    return message


async def send(websocket, message):
    await websocket.send(message.SerializeToString())
