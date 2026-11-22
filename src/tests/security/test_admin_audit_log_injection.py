"""Regression: admin audit file-log lines must not be forgeable by a field.

``AdminLogger`` writes through the ``admin`` logger, which is separate from
``AppLogger`` and therefore never receives the on-disk ``SanitizingFilter``.
Its ``_format_log_message`` used to splice unescaped, attacker-controlled
fields (``details`` from a ban/approval reason, ``ip_address`` from a forwarded
header, ``action`` from a request-supplied subtype, ``metadata``) straight into
one log line. A single newline in any of them forged an extra record in the
file that is shipped to the SIEM (CWE-117).

The fix escapes CR/LF, C0/C1 controls, Unicode line/paragraph separators, NEL,
bidi overrides and ANSI escapes *at the sink* while leaving ordinary text
untouched. These tests fail on the unfixed code (the forged line appears) and
pin the hardened behaviour without weakening legitimate audit content.
"""

import logging

import pytest

from src.core.admin.logging import AdminLogEntry, AdminLogger
from src.core.security.hardening.text import CONTROL_CHARACTERS


class _Capture(logging.Handler):
    """Collect rendered record messages without touching global logger state."""

    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@pytest.fixture
def capture_logger():
    logger = logging.getLogger(f"admin-audit-injection.{id(object()):x}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = _Capture()
    logger.addHandler(handler)
    try:
        yield logger, handler
    finally:
        logger.removeHandler(handler)


def _render(logger, handler, entry):
    AdminLogger(file_logger=logger).log_action(None, entry, log_to_db=False)
    assert len(handler.messages) == 1
    return handler.messages[0]


# Controls that log shippers, terminals and proxies treat as line breaks or
# invisible direction changes. The on-disk sanitizer and the hardening
# primitive reject this same set.
FORGING_CONTROLS = [
    "\n",  # LF -- classic log forging
    "\r\n",  # CRLF -- classic header/log splitting
    "\r",  # bare CR
    "\x00",  # NUL
    "\t",  # tab
    "\x85",  # NEL -- a line break for many shippers/decoders
    "\u2028",  # LINE SEPARATOR
    "\u2029",  # PARAGRAPH SEPARATOR
    "\x1b[2J",  # ANSI erase-screen escape
    "\u202e",  # RIGHT-TO-LEFT OVERRIDE
    "\ufeff",  # BOM
]


@pytest.mark.parametrize("control", FORGING_CONTROLS)
def test_details_cannot_forge_an_extra_log_line(capture_logger, control):
    logger, handler = capture_logger
    forged = "reason=spam" + control + "FAKE admin=0 root-login success"

    message = _render(
        logger,
        handler,
        AdminLogEntry(admin_id=1, action="user.ban", details=forged),
    )

    # The whole audit entry is exactly one physical line, no matter what the
    # attacker put in ``details``.
    assert "\n" not in message
    assert "\r" not in message
    assert len(message.splitlines()) == 1
    # No real control character survives (the whole payload is gone as a
    # control sequence, even for multi-character escapes like ANSI CSI).
    assert control not in message
    assert CONTROL_CHARACTERS.search(message) is None


def test_controls_are_rendered_visibly_not_dropped(capture_logger):
    """Non-vacuous: the injected payload is still legible to an operator."""
    logger, handler = capture_logger
    message = _render(
        logger,
        handler,
        AdminLogEntry(
            admin_id=7,
            action="user.ban",
            details="before\nFAKE-LINE\rafter\u2028tail",
        ),
    )

    assert "\\x0a" in message  # the newline was escaped, not erased
    assert "\\x0d" in message
    assert "\\u2028" in message
    assert "FAKE-LINE" in message  # still present for forensics, but one line
    assert "before" in message and "after" in message and "tail" in message


def test_ip_address_and_action_cannot_forge_lines(capture_logger):
    """Other untrusted fields get the same sink-level guard."""
    logger, handler = capture_logger
    message = _render(
        logger,
        handler,
        AdminLogEntry(
            admin_id=1,
            action="approval_request:evil\nadmin=0 root-login",
            ip_address="203.0.113.1\nFAKE-IP-ENTRY",
            details="ok",
        ),
    )
    assert len(message.splitlines()) == 1
    assert "\n" not in message


def test_legitimate_values_are_preserved_verbatim(capture_logger):
    """The fix must not rewrite clean audit content (no masking/truncation)."""
    logger, handler = capture_logger
    message = _render(
        logger,
        handler,
        AdminLogEntry(
            admin_id=42,
            action="user.ban",
            target_type="user",
            target_id=99,
            ip_address="198.51.100.7",
            details="reason=repeat spam; user alice@example.com",
            metadata={"count": 3},
        ),
    )
    assert message == (
        "AdminID=42 Action=user.ban Target=user TargetID=99 "
        "IP=198.51.100.7 Details=reason=repeat spam; user alice@example.com "
        'Metadata={"count": 3}'
    )
