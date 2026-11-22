"""Regression: control characters bypassing the aiortc SFU id allowlist.

``validate_identifier`` used ``_ID_RE.match()`` with a ``$`` anchor and returned
the caller's string unchanged. Python's ``$`` also matches just before a final
newline, so a peer id such as ``"peer-1\\n"`` passed the allowlist and was
returned *with* the newline. Peer ids are embedded verbatim in recording
filenames (``RecordingMixin.start_recording``) and in log lines, so the control
character escaped into a path component and forged extra log entries even
though the allowlist promised ``[A-Za-z0-9._:-]`` only.

These tests fail against the pre-fix validator (the crafted ids are accepted)
and pass once the shared control-character gate and ``fullmatch`` are applied.
"""

from __future__ import annotations

import re

import pytest

from src.core.security.hardening.text import CONTROL_CHARACTERS
from src.core.voice.signaling.sfu import aiortc_adapter as pkg
from src.core.voice.signaling.sfu.aiortc_adapter import (
    AIORTC_AVAILABLE,
    AiortcAdapter,
    validate_identifier,
)
from src.core.voice.signaling.sfu.aiortc_adapter.constants import _ID_RE_SRC
from src.core.voice.signaling.sfu.aiortc_adapter.errors import SFUValidationError


@pytest.mark.parametrize(
    "crafted",
    [
        "peer-1\n",  # the actual bypass: trailing LF matched by `$`
        "peer-1\r",
        "peer-1\x00",
        "peer-1\x1b",
        "peer-1\x7f",
        "peer-1\x85",  # NEL
        "peer-1\u2028",  # LINE SEPARATOR
        "peer-1\u2029",  # PARAGRAPH SEPARATOR
        "peer-1\u202e",  # bidi override
        "peer\n1",  # embedded newline
    ],
)
def test_identifier_rejects_control_characters(crafted: str) -> None:
    """No control character may survive the identifier allowlist gate."""
    with pytest.raises(SFUValidationError):
        validate_identifier(crafted, "peer_id")


def test_root_cause_prefix_match_vs_fullmatch() -> None:
    """Document why the old check let a trailing newline through.

    The legacy ``re.match(_ID_RE_SRC, value)`` accepts a final newline because
    ``$`` matches before it, while the fixed ``fullmatch`` does not.
    """
    assert re.match(_ID_RE_SRC, "peer-1\n") is not None
    assert re.fullmatch(_ID_RE_SRC, "peer-1\n") is None
    with pytest.raises(SFUValidationError):
        validate_identifier("peer-1\n", "peer_id")


class _LyingId(str):
    """A str subclass whose formatting disagrees with its real buffer."""

    def __str__(self) -> str:
        return "peer-1\n"

    def __format__(self, _spec: str) -> str:
        return "peer-1\n"


def test_str_subclass_cannot_smuggle_control_char_into_path() -> None:
    """The validator must return a fresh plain str, not the caller's object.

    Pre-fix the subclass itself was returned, so building a recording filename
    with an f-string invoked ``__format__`` and injected a newline.
    """
    validated = validate_identifier(_LyingId("peer-1"), "peer_id")
    assert type(validated) is str
    filename = f"rec_{validated}_producer.webm"
    assert CONTROL_CHARACTERS.search(filename) is None


class _FakePC:
    def __init__(self, *_args, **_kwargs) -> None:
        self._handlers: dict[str, object] = {}

    def on(self, event, handler=None):
        if handler is None:

            def deco(fn):
                self._handlers[event] = fn
                return fn

            return deco
        self._handlers[event] = handler
        return handler

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.skipif(not AIORTC_AVAILABLE, reason="aiortc is not installed")
async def test_newline_peer_id_never_reaches_recording_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """End-to-end: a crafted id is refused before any path can be built.

    Uses a fake peer connection and recorder and a temp output dir, so no real
    file is written. Against the pre-fix validator ``join_room`` accepts the
    newline id, which would later be embedded in a ``.webm`` filename.
    """
    captured_paths: list[str] = []

    class _FakeRecorder:
        def __init__(self, filepath: str) -> None:
            captured_paths.append(filepath)

        def addTrack(self, _track) -> None:
            return None

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    monkeypatch.setattr(pkg, "RTCPeerConnection", _FakePC)
    monkeypatch.setattr(pkg, "MediaRecorder", _FakeRecorder)

    adapter = AiortcAdapter()
    with pytest.raises(SFUValidationError):
        await adapter.join_room("ctrl-room", "peer-1\n")

    # The room was never created, so no recording path can be constructed and
    # nothing is written under the temp output dir.
    assert "ctrl-room" not in adapter._rooms
    assert captured_paths == []
    assert list(tmp_path.iterdir()) == []
