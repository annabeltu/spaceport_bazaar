"""
Tests for the six command builders (package D).

The core check: for every answer key in tests/fixtures/spec_messages/, the
matching builder -- called with the same placeholder values the answer key
uses -- must produce byte-for-byte the same SerializeToString() output. That
proves the builder writes exactly what the spec says, not just something that
"looks right" when printed.

This file is driven entirely from spec.SPEC_MESSAGES, so adding or removing
an answer key changes what gets tested here too, and all 8 stay covered.
"""
import pytest

from bazaar_client import commands
from bazaar_client.models import Amounts
from generated import bazaar_pb2 as pb
from spec import SPEC_MESSAGES, TEST_PLACEHOLDER_VALUES, load_spec_message

RUN_ID = TEST_PLACEHOLDER_VALUES["RUN_ID"]
ZERO_PRICE_OFFER_ID = TEST_PLACEHOLDER_VALUES["ZERO_PRICE_OFFER_ID"]
ADVERTISEMENT_ID = TEST_PLACEHOLDER_VALUES["ADVERTISEMENT_ID"]


# One entry per answer key: a zero-argument function that calls the builder
# with the same values the .textproto file uses. Keyed by filename so the
# parametrized test below can look up "what should this file's bytes match".
BUILDER_CALLS = {
    "01_ready.textproto": lambda: commands.build_ready(
        run_id=RUN_ID, snapshot_sequence=1, ready=True
    ),
    "02_advertise_water_for_food.textproto": lambda: commands.build_advertise(
        run_id=RUN_ID,
        request_id="student-advertise-1",
        selling=(pb.RESOURCE_WATER,),
        seeking=(pb.RESOURCE_FOOD,),
        expires_tick=6,
    ),
    "03_advertise_seeking_components.textproto": lambda: commands.build_advertise(
        run_id=RUN_ID,
        request_id="student-advertise-seeking-1",
        selling=(),
        seeking=(pb.RESOURCE_COMPONENTS,),
        expires_tick=6,
    ),
    "04_offer_water_for_food.textproto": lambda: commands.build_offer(
        run_id=RUN_ID,
        request_id="student-offer-1",
        recipient_id="P02",
        give=Amounts(water=2, food=0, components=0),
        receive=Amounts(water=0, food=1, components=0),
        expires_tick=6,
    ),
    "07_accept_gift.textproto": lambda: commands.build_accept(
        run_id=RUN_ID, request_id="student-accept-1", offer_id=ZERO_PRICE_OFFER_ID
    ),
    "08_withdraw_advertisement.textproto": lambda: commands.build_withdraw(
        run_id=RUN_ID, request_id="student-withdraw-1", object_id=ADVERTISEMENT_ID
    ),
    "09_advertise_over_request_limit.textproto": lambda: commands.build_advertise(
        run_id=RUN_ID,
        request_id="student-advertise-2",
        selling=(pb.RESOURCE_WATER,),
        seeking=(pb.RESOURCE_FOOD,),
        expires_tick=6,
    ),
    "10_sync.textproto": lambda: commands.build_sync(run_id=RUN_ID),
}


def spec_id(spec):
    return spec.filename


def test_every_answer_key_has_a_matching_builder_call():
    # If someone adds an answer key but forgets to wire up a builder call
    # here, this catches it with a clear message instead of the parametrized
    # test below silently having no case for that file.
    answer_key_files = {spec.filename for spec in SPEC_MESSAGES}
    assert answer_key_files == set(BUILDER_CALLS)


@pytest.mark.parametrize("spec", SPEC_MESSAGES, ids=spec_id)
def test_builder_matches_the_answer_key_byte_for_byte(spec):
    expected = load_spec_message(spec.filename)
    built = BUILDER_CALLS[spec.filename]()
    assert built.SerializeToString() == expected.SerializeToString()


@pytest.mark.parametrize("spec", SPEC_MESSAGES, ids=spec_id)
def test_builder_output_is_initialized(spec):
    # A message that isn't fully filled in can't be serialized at all, so
    # this is really a check that the byte comparison above wasn't
    # comparing two things that would both fail to encode.
    built = BUILDER_CALLS[spec.filename]()
    assert built.IsInitialized(), built.FindInitializationErrors()


# --- Builders return independent objects ----------------------------------


def test_two_calls_to_the_same_builder_return_independent_messages():
    first = commands.build_sync(run_id="run-a")
    second = commands.build_sync(run_id="run-b")
    assert first.sync.run_id == "run-a"
    assert second.sync.run_id == "run-b"


def test_mutating_one_built_message_does_not_affect_another():
    first = commands.build_ready(run_id=RUN_ID, snapshot_sequence=1, ready=True)
    second = commands.build_ready(run_id=RUN_ID, snapshot_sequence=1, ready=True)
    first.ready.snapshot_sequence = 99
    assert second.ready.snapshot_sequence == 1


# --- Watch out for empty lists: step 3's `selling {}` -----------------------


def test_advertise_with_empty_selling_still_marks_the_list_present():
    # In proto2 a required sub-message you never touch counts as MISSING, so
    # `selling=()` has to still produce a present-but-empty `selling {}`,
    # not an absent field. HasField is how we check "present" for a
    # sub-message, separately from whether its own list is empty.
    message = commands.build_advertise(
        run_id=RUN_ID,
        request_id="student-advertise-seeking-1",
        selling=(),
        seeking=(pb.RESOURCE_COMPONENTS,),
        expires_tick=6,
    )
    assert message.advertise.body.HasField("selling")
    assert list(message.advertise.body.selling.items) == []


# --- Each builder sets protocol_version and type itself ---------------------


def test_ready_sets_protocol_version_and_type():
    message = commands.build_ready(run_id=RUN_ID, snapshot_sequence=1, ready=True)
    assert message.ready.protocol_version == "2.0"
    assert message.ready.type == pb.READY_TYPE_READY


def test_advertise_sets_protocol_version_and_type():
    message = commands.build_advertise(
        run_id=RUN_ID,
        request_id="student-advertise-1",
        selling=(pb.RESOURCE_WATER,),
        seeking=(pb.RESOURCE_FOOD,),
        expires_tick=6,
    )
    assert message.advertise.protocol_version == "2.0"
    assert message.advertise.type == pb.ADVERTISE_TYPE_ADVERTISE


def test_offer_sets_protocol_version_and_type():
    message = commands.build_offer(
        run_id=RUN_ID,
        request_id="student-offer-1",
        recipient_id="P02",
        give=Amounts(water=2, food=0, components=0),
        receive=Amounts(water=0, food=1, components=0),
        expires_tick=6,
    )
    assert message.offer.protocol_version == "2.0"
    assert message.offer.type == pb.OFFER_COMMAND_TYPE_OFFER


def test_accept_sets_protocol_version_and_type():
    message = commands.build_accept(
        run_id=RUN_ID, request_id="student-accept-1", offer_id=ZERO_PRICE_OFFER_ID
    )
    assert message.accept.protocol_version == "2.0"
    assert message.accept.type == pb.ACCEPT_TYPE_ACCEPT


def test_withdraw_sets_protocol_version_and_type():
    message = commands.build_withdraw(
        run_id=RUN_ID, request_id="student-withdraw-1", object_id=ADVERTISEMENT_ID
    )
    assert message.withdraw.protocol_version == "2.0"
    assert message.withdraw.type == pb.WITHDRAW_TYPE_WITHDRAW


def test_sync_sets_protocol_version_and_type():
    message = commands.build_sync(run_id=RUN_ID)
    assert message.sync.protocol_version == "2.0"
    assert message.sync.type == pb.SYNC_TYPE_SYNC


def test_sync_has_no_request_id_field_touched():
    # The contract: "Sync has no body and no request_id."
    message = commands.build_sync(run_id=RUN_ID)
    assert "request_id" not in [f.name for f in message.sync.DESCRIPTOR.fields]
