"""Regression: an upload field must not forge a line in the media module log.

``src.core.media._upload`` logs the attacker-supplied filename (and the
declared content type) through a plain ``logging.getLogger(__name__)`` logger.
Unlike ``AppLogger``, that logger carries no ``SanitizingFilter``, so a control
character in the filename reaches the captured stdout/disk log verbatim.
``MediaManager._sanitize_filename`` only removes C0/DEL (``ord(c) < 32`` or
127); it deliberately leaves U+0085 (NEL), U+2028/U+2029 and the bidi controls
in place. Those are line breaks / invisible direction changes for log shippers
and for ``str.splitlines()``, so one crafted upload forges an extra audit line
(CWE-117).

These tests fail on the unfixed code (the forged part appears on its own line)
and pin the hardened behaviour without rewriting legitimate filenames.
"""

import logging

import pytest

from src.core.media import _upload as upload_mod

GIF_MAGIC = b"GIF89a" + b"\x00" * 32


class _Capture(logging.Handler):
    """Collect rendered record messages without touching global logger state."""

    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@pytest.fixture
def capture_upload_logger():
    logger = logging.getLogger(upload_mod.__name__)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = _Capture()
    logger.addHandler(handler)
    try:
        yield logger, handler
    finally:
        logger.removeHandler(handler)


def _forged(control):
    return "avatar" + control + "FAKE admin=0 root-login success.png"


@pytest.mark.parametrize("control", ["\u2028", "\u2029", "\x85"])
def test_filename_cannot_forge_an_extra_log_line(
    capture_upload_logger, media_manager, test_user, control
):
    logger, handler = capture_upload_logger
    from src.core.media.exceptions import FileTypeError

    with pytest.raises(FileTypeError):
        media_manager.upload_file(
            user_id=test_user.id,
            file_data=GIF_MAGIC,
            filename=_forged(control),
            content_type="image/png",
        )

    warnings = [m for m in handler.messages if "Magic byte validation" in m]
    assert warnings, "expected the magic-byte mismatch warning to be logged"
    message = warnings[0]

    # The whole warning is exactly one physical line, no matter what the
    # attacker put in the filename.
    assert len(message.splitlines()) == 1
    assert control not in message
    from src.core.security.hardening.text import CONTROL_CHARACTERS

    assert CONTROL_CHARACTERS.search(message) is None
    # The control is escaped visibly (not silently erased), and the attacker's
    # markers stay legible for forensics -- just no longer on their own line.
    assert f"\\u{ord(control):04x}" in message or f"\\x{ord(control):02x}" in message
    assert "FAKE admin=0 root-login success" in message


def test_legitimate_filename_is_preserved_verbatim(
    capture_upload_logger, media_manager, test_user
):
    logger, handler = capture_upload_logger
    from src.core.media.exceptions import FileTypeError

    with pytest.raises(FileTypeError):
        media_manager.upload_file(
            user_id=test_user.id,
            file_data=GIF_MAGIC,
            filename="holiday_photo.png",
            content_type="image/png",
        )

    warnings = [m for m in handler.messages if "Magic byte validation" in m]
    assert warnings
    message = warnings[0]
    assert "holiday_photo.png" in message
    assert message == (
        "Magic byte validation failed for holiday_photo.png (claimed: image/png)"
    )
