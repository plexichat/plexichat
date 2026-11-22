"""Regression: a remote federation error must not forge a line in the manager log.

``src.core.plexijoin.manager`` logs ``result["error"]`` from ``_post_signed``
through a plain ``logging.getLogger(__name__)`` logger. Unlike ``AppLogger``,
that logger carries no ``SanitizingFilter``, so a control character supplied by
the (semi-trusted, possibly hostile) remote instance reaches the captured
stdout/disk log verbatim. ``_post_signed`` derives that error from the remote
exchange/exception text with no control-character validation, so a U+0085 (NEL),
U+2028/U+2029 or bidi control forges an extra audit line (CWE-117).

These tests fail on the unfixed code (the forged part appears on its own line)
and pin the hardened behaviour without rewriting legitimate error text.
"""

import asyncio
import logging

import pytest

from src.core.plexijoin import manager as manager_mod


class _Capture(logging.Handler):
    """Collect rendered record messages without touching global logger state."""

    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@pytest.fixture
def capture_manager_logger():
    logger = logging.getLogger(manager_mod.__name__)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = _Capture()
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)


def _manager(error):
    mgr = manager_mod.PlexiJoinManager(None)
    connection = {
        "id": 1,
        "remote_url": "https://example.com",
        "shared_key": "key",
        "created_by": 1,
        "note": None,
        "status": "active",
    }
    mgr.get_connection = lambda cid: connection

    async def _failed_post(url, key, path, payload):
        return {"success": False, "error": error}

    mgr._post_signed = _failed_post
    return mgr


def _forged(control):
    return "remote refused" + control + "FAKE admin=0 root-login success"


def _assert_single_legible_line(message, control):
    assert len(message.splitlines()) == 1
    assert control not in message
    from src.core.security.hardening.text import CONTROL_CHARACTERS

    assert CONTROL_CHARACTERS.search(message) is None
    assert f"\\u{ord(control):04x}" in message or f"\\x{ord(control):02x}" in message
    assert "FAKE admin=0 root-login success" in message


@pytest.mark.parametrize("control", ["\u2028", "\u2029", "\x85"])
def test_join_request_error_cannot_forge_an_extra_log_line(
    capture_manager_logger, control
):
    asyncio.run(_manager(_forged(control)).send_join_request(1))

    warnings = [m for m in capture_manager_logger.messages if "delivery to" in m]
    assert warnings, "expected the join-request delivery warning to be logged"
    _assert_single_legible_line(warnings[0], control)


@pytest.mark.parametrize("control", ["\u2028", "\u2029", "\x85"])
def test_message_error_cannot_forge_an_extra_log_line(capture_manager_logger, control):
    asyncio.run(_manager(_forged(control)).send_message(1, "chat", {}))

    warnings = [
        m for m in capture_manager_logger.messages if "federated message to" in m
    ]
    assert warnings, "expected the federated-message warning to be logged"
    _assert_single_legible_line(warnings[0], control)


def test_legitimate_error_is_preserved_verbatim(capture_manager_logger):
    asyncio.run(_manager("remote returned 503").send_join_request(1))

    warnings = [m for m in capture_manager_logger.messages if "delivery to" in m]
    assert warnings
    message = warnings[0]
    assert len(message.splitlines()) == 1
    assert message == (
        "plexijoin: join request delivery to https://example.com failed: "
        "remote returned 503"
    )
