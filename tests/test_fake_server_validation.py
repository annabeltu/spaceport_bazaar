"""Adversarial validation tests for the independent fake server."""

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from generated import bazaar_pb2 as pb

from fake_server.scenario import Scenario
from fake_server.server import FakeServer
from spec import load_spec_message


SCRIPT = (
    "01_ready.textproto",
    "02_advertise_water_for_food.textproto",
    "03_advertise_seeking_components.textproto",
    "04_offer_water_for_food.textproto",
    "07_accept_gift.textproto",
    "08_withdraw_advertisement.textproto",
    "09_advertise_over_request_limit.textproto",
)


def _scenario_and_command(filename):
    scenario = Scenario.create("fake-run-1")
    placeholders = {"RUN_ID": scenario.run_id}
    for current in SCRIPT:
        command = load_spec_message(current, placeholder_values=placeholders)
        if current == filename:
            return scenario, command
        scenario, replies = scenario.handle(command)
        if current == "03_advertise_seeking_components.textproto":
            placeholders["ADVERTISEMENT_ID"] = replies[0].result.object_id.value
        if current == "04_offer_water_for_food.textproto":
            gift = next(
                offer
                for offer in replies[-1].state.offers.items
                if offer.proposer_id == "P02" and offer.status == pb.OFFER_STATUS_OPEN
            )
            placeholders["ZERO_PRICE_OFFER_ID"] = gift.offer_id
    raise AssertionError(f"fixture not in script: {filename}")


def _wrong_step_2(command):
    command.advertise.body.selling.items[0] = pb.RESOURCE_FOOD


def _wrong_step_3(command):
    command.advertise.body.selling.items.append(pb.RESOURCE_WATER)


def _wrong_step_4(command):
    command.offer.body.give.water = 1


def _wrong_step_7(command):
    command.accept.body.offer_id = "wrong-offer"


def _wrong_step_8(command):
    command.withdraw.body.object_id = "wrong-advertisement"


def _wrong_step_9(command):
    command.advertise.body.expires_tick = 5


@pytest.mark.parametrize(
    ("filename", "alter"),
    (
        ("02_advertise_water_for_food.textproto", _wrong_step_2),
        ("03_advertise_seeking_components.textproto", _wrong_step_3),
        ("04_offer_water_for_food.textproto", _wrong_step_4),
        ("07_accept_gift.textproto", _wrong_step_7),
        ("08_withdraw_advertisement.textproto", _wrong_step_8),
        ("09_advertise_over_request_limit.textproto", _wrong_step_9),
    ),
)
def test_correct_request_id_with_wrong_body_is_scenario_mismatch(filename, alter):
    scenario, command = _scenario_and_command(filename)
    alter(command)

    with pytest.raises(ValueError, match="scenario mismatch"):
        scenario.handle(command)


@pytest.mark.asyncio
async def test_new_connection_fences_an_open_old_connection():
    async with FakeServer() as server:
        options = {
            "additional_headers": {"Authorization": f"Bearer {server.token}"},
            "subprotocols": ["bazaar.protobuf.v2"],
        }
        async with connect(server.url, **options) as first:
            await first.recv()
            ready = load_spec_message(
                "01_ready.textproto", placeholder_values={"RUN_ID": server.run_id}
            )
            await first.send(ready.SerializeToString())
            await first.recv()

            async with connect(server.url, **options) as second:
                await second.recv()
                fenced = pb.ServerMessage.FromString(await first.recv())
                assert fenced.protocol_error.code == pb.CONTROL_CODE_SESSION_FENCED
                assert fenced.protocol_error.close_session is True
                with pytest.raises(ConnectionClosed):
                    await first.recv()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_frame",
    (
        b"oversized",
        b"unknown-field",
        b"duplicate-field",
        b"unknown-enum",
    ),
)
async def test_wire_format_violations_are_bad_messages(bad_frame):
    async with FakeServer() as server:
        async with connect(
            server.url,
            additional_headers={"Authorization": f"Bearer {server.token}"},
            subprotocols=["bazaar.protobuf.v2"],
            max_size=None,
        ) as websocket:
            await websocket.recv()
            ready = load_spec_message(
                "01_ready.textproto", placeholder_values={"RUN_ID": server.run_id}
            ).SerializeToString()
            inner = bytearray(ready[2:])
            duplicate = bytes(inner) + b"\x12\x03" + b"1.0"
            unknown_enum = bytearray(inner)
            unknown_enum[1] = 99
            frames = {
                "oversized": ready + b"\x00" * (16_385 - len(ready)),
                "unknown-field": ready + b"\xa0\x06\x01",
                "duplicate-field": b"\x32" + bytes([len(duplicate)]) + duplicate,
                "unknown-enum": b"\x32" + bytes([len(unknown_enum)]) + unknown_enum,
            }
            await websocket.send(frames[bad_frame.decode()])
            response = pb.ServerMessage.FromString(await websocket.recv())
            assert response.protocol_error.code == pb.CONTROL_CODE_BAD_MESSAGE
            assert response.protocol_error.close_session is False
