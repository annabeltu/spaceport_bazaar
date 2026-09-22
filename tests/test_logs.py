"""Tests for readable, token-safe client logging."""

from bazaar_client import logs
from spec import TEST_PLACEHOLDER_VALUES, load_spec_message


def test_redact_hides_known_token_and_bearer_values():
    token = "private-practice-token"
    text = f"token={token} Authorization: Bearer another-secret trailing"

    redacted = logs.redact(text, token)

    assert token not in redacted
    assert "another-secret" not in redacted
    assert redacted.count(logs.REDACTED) == 2


def test_setup_logging_writes_redacted_console_and_file(tmp_path, capsys):
    path = tmp_path / "client.log"
    token = "private-practice-token"
    logger = logs.setup_logging(path, token)

    logger.info("Authorization: Bearer %s", token)

    console = capsys.readouterr().err
    written = path.read_text()
    assert token not in console + written
    assert logs.REDACTED in console
    assert logs.REDACTED in written


def test_setup_logging_replaces_old_handlers(tmp_path):
    first = tmp_path / "first.log"
    second = tmp_path / "second.log"
    logs.setup_logging(first, None)
    logger = logs.setup_logging(second, None)

    logger.info("once")

    assert "once" not in first.read_text()
    assert second.read_text().count("once") == 1


def test_message_logs_are_one_line_and_labeled(tmp_path):
    path = tmp_path / "messages.log"
    logger = logs.setup_logging(path, None)
    sent = load_spec_message("01_ready.textproto", TEST_PLACEHOLDER_VALUES)
    received = __import__("factories").make_readiness()

    logs.log_sent(logger, sent)
    logs.log_received(logger, received)

    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert logs.SENT_LABEL in lines[0]
    assert logs.RECEIVED_LABEL in lines[1]
