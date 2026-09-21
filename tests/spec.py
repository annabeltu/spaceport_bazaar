"""
Facts from the starter spec, and its example messages as protobuf objects.

The files in tests/fixtures/spec_messages/ are the spec's example messages,
copied verbatim from bazaar-protobuf-starter-linux/README.md. They are our
ANSWER KEYS: in Phase 3, the client's message builders must produce exactly
these messages.
"""
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from google.protobuf import text_format

from generated import bazaar_pb2 as pb

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "spec_messages"
SPEC_README = REPO_ROOT / "bazaar-protobuf-starter-linux" / "README.md"

# --- Limits stated in the spec ----------------------------------------------

# "Keep commands within 16,384 bytes." Phase 3's client reads the live limit
# from the server (State.rules.max_command_bytes) instead of trusting a copy.
SPEC_MAX_COMMAND_BYTES = 16_384

# "Valid IDs contain 1-64 letters, digits, underscores, or hyphens."
SPEC_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")

SPEC_PROTOCOL_VERSION = "2.0"

# --- The example messages ----------------------------------------------------


@dataclass(frozen=True)  # frozen = read-only after creation
class SpecMessage:
    """One example message from the spec."""

    filename: str
    step: int  # the spec's step number
    command: str  # which ClientMessage field it sets, e.g. "offer"


# In the same order they appear in the spec.
SPEC_MESSAGES = (
    SpecMessage("01_ready.textproto", 1, "ready"),
    SpecMessage("02_advertise_water_for_food.textproto", 2, "advertise"),
    SpecMessage("03_advertise_seeking_components.textproto", 3, "advertise"),
    SpecMessage("04_offer_water_for_food.textproto", 4, "offer"),
    SpecMessage("07_accept_gift.textproto", 7, "accept"),
    SpecMessage("08_withdraw_advertisement.textproto", 8, "withdraw"),
    SpecMessage("09_advertise_over_request_limit.textproto", 9, "advertise"),
    SpecMessage("10_sync.textproto", 10, "sync"),
)

# The spec writes values that only exist at runtime as placeholders, like
# "<RUN_ID>". Tests fill them in with these stand-ins.
PLACEHOLDER_PATTERN = re.compile(r"<([A-Z_]+)>")

# MappingProxyType makes this dictionary read-only, so no test can change
# the stand-in values out from under another test.
TEST_PLACEHOLDER_VALUES = MappingProxyType(
    {
        "RUN_ID": "test-run-1",
        "ZERO_PRICE_OFFER_ID": "test-gift-offer-1",
        "ADVERTISEMENT_ID": "test-advertisement-1",
    }
)


def load_spec_message(filename, placeholder_values=TEST_PLACEHOLDER_VALUES):
    """Parse one answer key into a new ClientMessage, filling in placeholders.

    Raises ValueError if the file uses a placeholder that has no value, so a
    literal "<RUN_ID>" can never end up inside a message.
    """
    text = (FIXTURE_DIR / filename).read_text()

    def fill_in(match):
        name = match.group(1)
        if name not in placeholder_values:
            raise ValueError(
                f"{filename} uses placeholder <{name}>, but no value was given"
            )
        return placeholder_values[name]

    filled_text = PLACEHOLDER_PATTERN.sub(fill_in, text)
    return text_format.Parse(filled_text, pb.ClientMessage())


def inner_command(message):
    """The command inside a ClientMessage, e.g. its `offer` part."""
    return getattr(message, message.WhichOneof("message"))


def read_fixture_body(filename):
    """An answer key's text without its '#' header comment lines."""
    lines = (FIXTURE_DIR / filename).read_text().splitlines(keepends=True)
    return "".join(line for line in lines if not line.startswith("#"))


def extract_readme_examples():
    """Every example-message code block in the spec README, in order."""
    return re.findall(
        r"```textproto(?:-ready)?\n(.*?)```", SPEC_README.read_text(), flags=re.S
    )
