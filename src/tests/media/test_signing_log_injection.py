"""Regression: an S3 fallback filename must not forge a line in the signing log.

``src.core.media._signing`` logs the caller-supplied ``file.filename`` through a
plain ``logging.getLogger(__name__)`` logger. Unlike ``AppLogger``, that logger
carries no ``SanitizingFilter``, so a control character in the filename reaches
the captured stdout/disk log verbatim. Upload sanitisation only removes C0/DEL;
it deliberately leaves U+0085 (NEL), U+2028/U+2029 and the bidi controls in
place. Those are line breaks / invisible direction changes for log shippers and
for ``str.splitlines()``, so one crafted filename forges an extra audit line
(CWE-117).

These tests fail on the unfixed code (the forged part appears on its own line)
and pin the hardened behaviour without rewriting legitimate filenames.
"""

import logging

import pytest

from src.core.media import _signing as signing_mod
from src.core.media.models import StorageBackend, SignedUrl


class _Capture(logging.Handler):
    """Collect rendered record messages without touching global logger state."""

    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@pytest.fixture
def capture_signing_logger():
    logger = logging.getLogger(signing_mod.__name__)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = _Capture()
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)


class _StubSigner:
    def sign_url(self, url, file_id, expires_in=None, user_id=None):
        return SignedUrl(url=url, expires_at=0, signature="test", file_id=file_id)


class _StubStorage:
    def is_encrypted(self, path):
        return False

    # Deliberately no generate_presigned_url: forces the fallback log line.


class _StubFile:
    def __init__(self, filename):
        self.filename = filename
        self.storage_path = "key"
        self.storage_backend = StorageBackend.S3


def _manager(filename):
    harness = signing_mod._SigningMixin()
    harness._url_signer = _StubSigner()
    harness.get_file = lambda file_id: _StubFile(filename)
    harness._get_storage_by_backend = lambda backend: _StubStorage()
    return harness


def _forged(control):
    return "avatar" + control + "FAKE admin=0 root-login success.png"


@pytest.mark.parametrize("control", ["\u2028", "\u2029", "\x85"])
def test_filename_cannot_forge_an_extra_log_line(capture_signing_logger, control):
    handler = capture_signing_logger

    _manager(_forged(control)).sign_url(1)

    warnings = [m for m in handler.messages if "S3 native signing unavailable" in m]
    assert warnings, "expected the S3 fallback warning to be logged"
    message = warnings[0]

    assert len(message.splitlines()) == 1
    assert control not in message
    from src.core.security.hardening.text import CONTROL_CHARACTERS

    assert CONTROL_CHARACTERS.search(message) is None
    assert f"\\u{ord(control):04x}" in message or f"\\x{ord(control):02x}" in message
    assert "FAKE admin=0 root-login success" in message


def test_legitimate_filename_is_preserved_verbatim(capture_signing_logger):
    handler = capture_signing_logger

    _manager("holiday_photo.png").sign_url(1)

    warnings = [m for m in handler.messages if "S3 native signing unavailable" in m]
    assert warnings
    message = warnings[0]
    assert len(message.splitlines()) == 1
    assert message == (
        "S3 native signing unavailable for holiday_photo.png, falling back to proxy"
    )
