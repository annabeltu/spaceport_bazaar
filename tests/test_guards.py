"""
Tests for guards.check() (package E): the pre-send safety checks.

check() is the ONLY way to get bytes the connection will send, so every one
of the 9 checks in its docstring needs its own proof here: one test showing
it blocks a bad message, and one showing it lets the matching good message
through. Part 2 of tests/test_protobuf_safety_net.py lists the gaps protobuf
leaves open; each of those gets its own test too, showing this file's checks
close it.

Test messages are built with spec.load_spec_message() or protobuf directly,
never with commands.py's builders -- package D is building those builders at
the same time as this file, in a different worktree.
"""
from types import MappingProxyType

import pytest

from bazaar_client.errors import GuardError
from bazaar_client.guards import _string_fields, check
from bazaar_client.models import MAX_UINT64, Amounts, GuardContext
from generated import bazaar_pb2 as pb
from spec import SPEC_MAX_COMMAND_BYTES, TEST_PLACEHOLDER_VALUES, load_spec_message

RUN_ID = TEST_PLACEHOLDER_VALUES["RUN_ID"]
FULL_INVENTORY = Amounts(water=30, food=30, components=30)

# Every answer key, in the same order tests/spec.py lists them.
ANSWER_KEY_FILES = (
    "01_ready.textproto",
    "02_advertise_water_for_food.textproto",
    "03_advertise_seeking_components.textproto",
    "04_offer_water_for_food.textproto",
    "07_accept_gift.textproto",
    "08_withdraw_advertisement.textproto",
    "09_advertise_over_request_limit.textproto",
    "10_sync.textproto",
)


def _context(
    *,
    run_id=RUN_ID,
    is_ready=True,
    max_command_bytes=SPEC_MAX_COMMAND_BYTES,
    inventory=FULL_INVENTORY,
    sent_requests=None,
):
    """A GuardContext that lets a normal spec message through, unless a test
    deliberately overrides one value to make a specific check fail.

    sent_requests is wrapped in MappingProxyType because that's what
    GuardContext expects (see models.py): a read-only mapping, so guards.py
    can never accidentally change the caller's dict.
    """
    return GuardContext(
        run_id=run_id,
        is_ready=is_ready,
        max_command_bytes=max_command_bytes,
        inventory=inventory,
        sent_requests=MappingProxyType(dict(sent_requests or {})),
    )


def _copy(message):
    """A copy of `message`, so a test can mutate it without changing the
    original. Same pattern as without_field() in test_protobuf_safety_net.py."""
    copy = type(message)()
    copy.CopyFrom(message)
    return copy


# --- All 8 answer keys, and the "never mutates" guarantee -----------------------


@pytest.mark.parametrize("filename", ANSWER_KEY_FILES)
def test_every_answer_key_passes_check_with_a_matching_context(filename):
    message = load_spec_message(filename)
    assert check(message, _context()) == message.SerializeToString()


@pytest.mark.parametrize("filename", ANSWER_KEY_FILES)
def test_check_never_changes_the_message_it_is_given(filename):
    message = load_spec_message(filename)
    before = message.SerializeToString()
    check(message, _context())
    after = message.SerializeToString()
    assert before == after


# --- Check 1: exactly one command is selected ------------------------------------


def test_check1_blocks_a_message_with_no_command_selected():
    with pytest.raises(GuardError, match="no command selected"):
        check(pb.ClientMessage(), _context())


def test_check1_allows_a_message_with_a_command_selected():
    message = load_spec_message("10_sync.textproto")
    assert check(message, _context())


# --- Check 2: every required field is set ----------------------------------------


def test_check2_blocks_a_missing_required_field():
    # The classic trap the spec warns about: giving water still needs an
    # explicit food: 0, or the field is simply absent (not zero -- absent).
    message = load_spec_message("04_offer_water_for_food.textproto")
    message.offer.body.give.ClearField("food")
    with pytest.raises(GuardError, match="missing required fields"):
        check(message, _context())


def test_check2_error_names_the_missing_field():
    message = load_spec_message("04_offer_water_for_food.textproto")
    message.offer.body.give.ClearField("food")
    with pytest.raises(GuardError, match="offer.body.give.food"):
        check(message, _context())


def test_check2_allows_a_message_with_every_required_field_set():
    message = load_spec_message("04_offer_water_for_food.textproto")
    assert check(message, _context())


# --- Check 3: protocol_version is "2.0" ------------------------------------------


def test_check3_blocks_the_wrong_protocol_version():
    message = load_spec_message("10_sync.textproto")
    message.sync.protocol_version = "1.0"
    with pytest.raises(GuardError, match="protocol_version"):
        check(message, _context())


def test_check3_allows_protocol_version_2_0():
    message = load_spec_message("10_sync.textproto")
    assert check(message, _context())


# --- Check 4a: run_id equals context.run_id --------------------------------------


def test_check4a_blocks_a_run_id_that_does_not_match_the_context():
    message = load_spec_message("10_sync.textproto")
    message.sync.run_id = "some-other-run"
    with pytest.raises(GuardError, match="run_id"):
        check(message, _context())


def test_check4a_allows_a_run_id_that_matches_the_context():
    message = load_spec_message("10_sync.textproto")
    assert check(message, _context())


# --- Check 4b: no field still holds a spec placeholder ---------------------------


def test_check4b_blocks_a_placeholder_left_in_a_field_other_than_run_id():
    # run_id is correct here on purpose, so this failure can only be the
    # placeholder scan, not the run_id check above.
    message = load_spec_message("07_accept_gift.textproto")
    message.accept.body.offer_id = "<ZERO_PRICE_OFFER_ID>"  # never filled in
    with pytest.raises(GuardError, match="placeholder"):
        check(message, _context())


def test_check4b_allows_a_message_with_no_placeholders_left():
    message = load_spec_message("07_accept_gift.textproto")
    assert check(message, _context())


# --- Check 5: request_id is 1-64 letters, digits, "_" or "-" ---------------------


@pytest.mark.parametrize(
    "bad_request_id",
    ["", "has spaces!", "x" * 65],
    ids=["empty", "punctuation", "too-long"],
)
def test_check5_blocks_a_badly_formatted_request_id(bad_request_id):
    message = load_spec_message("02_advertise_water_for_food.textproto")
    message.advertise.request_id = bad_request_id
    with pytest.raises(GuardError, match="request_id"):
        check(message, _context())


def test_check5_allows_a_well_formatted_request_id():
    message = load_spec_message("02_advertise_water_for_food.textproto")
    assert check(message, _context())


def test_check5_skips_commands_that_have_no_request_id():
    # sync and ready have no request_id field at all -- nothing to check.
    message = load_spec_message("10_sync.textproto")
    assert check(message, _context())


# --- Check 6: serialized size is at most context.max_command_bytes --------------


def test_check6_blocks_a_message_over_the_context_size_limit():
    message = load_spec_message("10_sync.textproto")
    with pytest.raises(GuardError, match="too large"):
        check(message, _context(max_command_bytes=1))


def test_check6_allows_a_message_within_the_context_size_limit():
    message = load_spec_message("10_sync.textproto")
    assert check(message, _context(max_command_bytes=SPEC_MAX_COMMAND_BYTES))


def test_check6_allows_a_message_exactly_at_the_size_limit():
    # The check is "> limit", so a message that lands exactly on the limit
    # must still be allowed -- this pins down that boundary.
    message = load_spec_message("10_sync.textproto")
    exact_limit = len(message.SerializeToString())
    assert check(message, _context(max_command_bytes=exact_limit))


# --- Check 7: trading commands wait for readiness --------------------------------


def test_check7_blocks_a_trading_command_before_readiness():
    message = load_spec_message("02_advertise_water_for_food.textproto")
    with pytest.raises(GuardError, match="not ready"):
        check(message, _context(is_ready=False))


def test_check7_allows_a_trading_command_once_ready():
    message = load_spec_message("02_advertise_water_for_food.textproto")
    assert check(message, _context(is_ready=True))


@pytest.mark.parametrize("filename", ["01_ready.textproto", "10_sync.textproto"])
def test_check7_always_allows_ready_and_sync_even_when_not_ready(filename):
    message = load_spec_message(filename)
    assert check(message, _context(is_ready=False))


# --- Check 8: a reused request_id must be byte-identical (an exact retry) -------


def test_check8_blocks_a_reused_request_id_with_different_bytes():
    original = load_spec_message("02_advertise_water_for_food.textproto")
    original_bytes = original.SerializeToString()
    changed = _copy(original)
    changed.advertise.body.expires_tick = 999  # same request_id, different command
    context = _context(sent_requests={"student-advertise-1": original_bytes})
    with pytest.raises(GuardError, match="reused"):
        check(changed, context)


def test_check8_allows_an_exact_retry():
    original = load_spec_message("02_advertise_water_for_food.textproto")
    original_bytes = original.SerializeToString()
    retry = _copy(original)
    context = _context(sent_requests={"student-advertise-1": original_bytes})
    assert check(retry, context) == original_bytes


def test_check8_allows_a_request_id_not_seen_before():
    message = load_spec_message("02_advertise_water_for_food.textproto")
    assert check(message, _context(sent_requests={}))


# --- Check 9: an offer's give never exceeds the inventory ------------------------


def test_check9_blocks_an_offer_that_gives_more_than_the_inventory():
    message = load_spec_message("04_offer_water_for_food.textproto")  # gives 2 water
    poor_context = _context(inventory=Amounts(water=1, food=30, components=30))
    with pytest.raises(GuardError, match="water"):
        check(message, poor_context)


def test_check9_allows_an_offer_within_the_inventory():
    message = load_spec_message("04_offer_water_for_food.textproto")  # gives 2 water
    context = _context(inventory=Amounts(water=2, food=30, components=30))
    assert check(message, context)


# --- Part 2 gaps: tests/test_protobuf_safety_net.py, mirrored and now blocked ---


def test_gap_empty_client_message_is_blocked():
    with pytest.raises(GuardError, match="no command selected"):
        check(pb.ClientMessage(), _context())


def test_gap_empty_run_id_is_blocked():
    message = load_spec_message("10_sync.textproto")
    message.sync.run_id = ""
    with pytest.raises(GuardError, match="run_id"):
        check(message, _context())


def test_gap_leftover_placeholder_is_blocked():
    # Mirrors the exact gap from test_protobuf_safety_net.py: run_id left as
    # the literal text "<RUN_ID>". No match= here on purpose: check 4a (run_id
    # equality) runs before check 4b (the placeholder scan) and "<RUN_ID>"
    # is also simply the wrong run_id, so check 4a is what actually fires --
    # this test only proves the gap is blocked, not which check does it.
    # test_check4b_blocks_a_placeholder_left_in_a_field_other_than_run_id
    # above isolates check 4b itself, using a field other than run_id.
    message = load_spec_message("10_sync.textproto")
    message.sync.run_id = "<RUN_ID>"
    with pytest.raises(GuardError):
        check(message, _context())


def test_gap_badly_formatted_request_id_is_blocked():
    message = load_spec_message("04_offer_water_for_food.textproto")
    message.offer.request_id = "has spaces and punctuation!"
    with pytest.raises(GuardError, match="request_id"):
        check(message, _context())


def test_gap_oversized_message_is_blocked():
    # Bloats recipient_id, not request_id, so this test proves the SIZE
    # check catches it -- request_id stays valid, so check 5 doesn't fire
    # first and hide what's being tested here.
    message = load_spec_message("04_offer_water_for_food.textproto")
    message.offer.body.recipient_id = "x" * 20_000
    with pytest.raises(GuardError, match="too large"):
        check(message, _context())


def test_gap_offering_more_than_you_own_is_blocked():
    message = load_spec_message("04_offer_water_for_food.textproto")
    message.offer.body.give.water = MAX_UINT64
    with pytest.raises(GuardError, match="water"):
        check(message, _context())


# --- _string_fields(): the helper the placeholder scan (check 4b) relies on ----


def test_string_fields_recurses_into_a_repeated_sub_message():
    # No field reachable from ClientMessage is a repeated sub-message today,
    # so check 4b's own tests above can't exercise this path. pb.DirectoryEntry
    # (unrelated to ClientMessage) does have one, so this proves the helper
    # itself is correct in general -- not just for today's schema -- and
    # would still find a placeholder even if a future command grows a
    # repeated body.
    directory = pb.ListDirectoryEntry()
    directory.items.add(station_id="P01", display_name="ok")
    directory.items.add(station_id="<RUN_ID>", display_name="ok")
    found = list(_string_fields(directory))
    assert (("items", "station_id"), "<RUN_ID>") in found
