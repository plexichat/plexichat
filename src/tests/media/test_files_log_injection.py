"""Regression: a thumbnail filename must not forge a line in the files log.

``src.core.media._files`` logs a caller-supplied ``filename`` through a plain
``logging.getLogger(__name__)`` logger. Unlike ``AppLogger``, that logger
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

from src.core.media import _files as files_mod


class _Capture(logging.Handler):
    """Collect rendered record messages without touching global logger state."""

    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@pytest.fixture
def capture_files_logger():
    logger = logging.getLogger(files_mod.__name__)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = _Capture()
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)


class _Meta:
    uploaded_by = 1


class _Cache:
    def get_by_id(self, file_id):
        return None

    def get_by_filename(self, filename):
        # Uploader fast path: lets the function return right after the
        # thumbnail branch so the test isolates the log line.
        return _Meta()


class _BrokenDB:
    def fetch_one(self, *args, **kwargs):
        raise RuntimeError("boom")


def _manager():
    harness = files_mod._FilesMixin()
    harness._cache = _Cache()
    harness._db = _BrokenDB()

    def _file_meta():
        return harness._cache

    harness._file_meta = _file_meta
    return harness


def _forged(control):
    return "thumbnails/123/avatar" + control + "FAKE admin=0 root-login success.png"


@pytest.mark.parametrize("control", ["\u2028", "\u2029", "\x85"])
def test_filename_cannot_forge_an_extra_log_line(capture_files_logger, control):
    handler = capture_files_logger
    forged = _forged(control)

    _manager().check_file_access(forged, 1)

    warnings = [m for m in handler.messages if "Error checking thumbnail access" in m]
    assert warnings, "expected the thumbnail-access warning to be logged"
    message = warnings[0]

    assert len(message.splitlines()) == 1
    assert control not in message
    from src.core.security.hardening.text import CONTROL_CHARACTERS

    assert CONTROL_CHARACTERS.search(message) is None
    assert f"\\u{ord(control):04x}" in message or f"\\x{ord(control):02x}" in message
    assert "FAKE admin=0 root-login success" in message
    assert forged.split(control)[0] in message


def test_legitimate_filename_is_preserved_verbatim(capture_files_logger):
    handler = capture_files_logger

    _manager().check_file_access("thumbnails/123/holiday_photo.png", 1)

    warnings = [m for m in handler.messages if "Error checking thumbnail access" in m]
    assert warnings
    message = warnings[0]
    assert len(message.splitlines()) == 1
    assert "thumbnails/123/holiday_photo.png" in message
    assert message == (
        "Error checking thumbnail access for thumbnails/123/holiday_photo.png: boom"
    )
