"""
What the protobuf library catches for us -- and what it doesn't.

Part 1 proves the free safety net works: protobuf refuses to turn an
incomplete or invalid message into bytes, so it can never be sent.

Part 2 documents the GAPS: mistakes protobuf happily serializes. Each one
is a check the client's pre-send guards must make in Phase 3. If a future
protobuf upgrade closes a gap, its test here fails -- that's a prompt to
revisit the guard, not a bug.
"""
import pytest
from google.protobuf import text_format
from google.protobuf.message import EncodeError

from generated import bazaar_pb2 as pb
from spec import (
    SPEC_MAX_COMMAND_BYTES,
    SPEC_MESSAGES,
    SPEC_REQUEST_ID_PATTERN,
    load_spec_message,
)

MAX_UINT64 = 2**64 - 1


def required_field_paths(message, prefix=()):
    """Yield the path to every required field in `message`.

    A path is a tuple of field names, like ("offer", "body", "give", "food").
    Walks into sub-messages that are set, so nested fields are included.
    """
    for field in message.DESCRIPTOR.fields:
        path = (*prefix, field.name)
        if field.is_required:
            yield path
        is_single_sub_message = field.message_type is not None and not field.is_repeated
        if is_single_sub_message and message.HasField(field.name):
            yield from required_field_paths(getattr(message, field.name), path)


def without_field(message, path):
    """Return a COPY of `message` with the field at `path` removed.

    The original message is left untouched.
    """
    copy = type(message)()
    copy.CopyFrom(message)
    parent = copy
    for name in path[:-1]:
        parent = getattr(parent, name)
    parent.ClearField(path[-1])
    return copy


# One test case per (answer key, required field) pair.
REQUIRED_FIELD_CASES = tuple(
    pytest.param(spec.filename, path, id=f"{spec.filename}:{'.'.join(path)}")
    for spec in SPEC_MESSAGES
    for path in required_field_paths(load_spec_message(spec.filename))
)


# --- Checking the helpers (the tests below depend on them) ---------------------


def test_required_field_paths_finds_all_three_resource_amounts():
    bundle = pb.Bundle(water=1, food=2, components=3)
    assert list(required_field_paths(bundle)) == [
        ("water",),
        ("food",),
        ("components",),
    ]


def test_required_field_paths_reaches_nested_fields():
    offer = load_spec_message("04_offer_water_for_food.textproto")
    assert ("offer", "body", "give", "food") in set(required_field_paths(offer))


@pytest.mark.parametrize("spec", SPEC_MESSAGES, ids=lambda spec: spec.filename)
def test_every_answer_key_has_required_fields_to_check(spec):
    # Guards against a silent failure: if the helper ever found nothing, the
    # big parametrized test below would have zero cases and "pass" while
    # testing nothing at all.
    message = load_spec_message(spec.filename)
    assert len(list(required_field_paths(message))) >= 3  # type, version, run_id


def test_without_field_leaves_the_original_untouched():
    original = load_spec_message("04_offer_water_for_food.textproto")
    snapshot = original.SerializeToString()
    without_field(original, ("offer", "body", "give", "food"))
    assert original.SerializeToString() == snapshot


# --- Part 1: caught by protobuf ------------------------------------------------


def test_forgetting_zero_amounts_is_caught():
    # The trap the spec warns about: "give 2 water" must still say food: 0
    # and components: 0.
    offer = pb.ClientMessage()
    offer.offer.body.give.water = 2
    assert "offer.body.give.food" in offer.FindInitializationErrors()
    assert "offer.body.give.components" in offer.FindInitializationErrors()


@pytest.mark.parametrize(("filename", "path"), REQUIRED_FIELD_CASES)
def test_leaving_out_any_required_field_blocks_serialization(filename, path):
    broken = without_field(load_spec_message(filename), path)
    assert not broken.IsInitialized()
    with pytest.raises(EncodeError):
        broken.SerializeToString()


def test_negative_amount_is_rejected():
    bundle = pb.Bundle()
    with pytest.raises(ValueError):
        bundle.water = -1


def test_amount_too_large_for_64_bits_is_rejected():
    bundle = pb.Bundle()
    with pytest.raises(ValueError):
        bundle.water = MAX_UINT64 + 1


def test_largest_64_bit_amount_survives_a_round_trip():
    # The spec: "preserve full 64-bit integers."
    bundle = pb.Bundle(water=MAX_UINT64, food=0, components=0)
    assert pb.Bundle.FromString(bundle.SerializeToString()).water == MAX_UINT64


def test_unknown_enum_value_is_rejected():
    ready = pb.Ready()
    with pytest.raises(ValueError):
        ready.type = 99


def test_misspelled_field_name_is_rejected_when_parsing_text():
    with pytest.raises(text_format.ParseError):
        text_format.Parse("offer { tpye: OFFER_COMMAND_TYPE_OFFER }", pb.ClientMessage())


# --- Part 2: NOT caught by protobuf -- gaps the Phase 3 guards must close -------


def test_gap_an_empty_client_message_serializes_to_zero_bytes():
    # Guard needed: exactly one command must be chosen.
    assert pb.ClientMessage().SerializeToString() == b""


def test_gap_an_empty_run_id_serializes():
    # Guard needed: run_id must match the current run.
    message = load_spec_message("10_sync.textproto")
    message.sync.run_id = ""
    assert message.IsInitialized()
    message.SerializeToString()  # no error


def test_gap_a_leftover_placeholder_serializes():
    # Guard needed: never send a spec placeholder like "<RUN_ID>".
    message = load_spec_message("10_sync.textproto")
    message.sync.run_id = "<RUN_ID>"
    message.SerializeToString()  # no error


def test_gap_a_badly_formatted_request_id_serializes():
    # Guard needed: request_id must be 1-64 letters, digits, "_" or "-".
    message = load_spec_message("04_offer_water_for_food.textproto")
    message.offer.request_id = "has spaces and punctuation!"
    assert not SPEC_REQUEST_ID_PATTERN.fullmatch(message.offer.request_id)
    message.SerializeToString()  # no error


def test_gap_an_oversized_message_serializes():
    # Guard needed: size must be within the server's max_command_bytes.
    message = load_spec_message("04_offer_water_for_food.textproto")
    message.offer.request_id = "x" * 20_000
    assert len(message.SerializeToString()) > SPEC_MAX_COMMAND_BYTES


def test_gap_offering_more_than_you_own_serializes():
    # Guard needed: protobuf has no idea what's in your inventory.
    message = load_spec_message("04_offer_water_for_food.textproto")
    message.offer.body.give.water = MAX_UINT64
    message.SerializeToString()  # no error
