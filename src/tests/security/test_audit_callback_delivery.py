"""Tests for sweep H: client media callbacks no server handler consumes.

Sweep H compares what the client *sends* on a voice opcode against what the
gateway handler *reads*. It exists because two real gaps lived in that space and
neither had any other detector: the peer speaking indicator (``speaking`` /
``volume``) and the recording consent (``consented``), the latter while the only
writer of ``consented_participants`` had no caller at all.

The important non-detection case is the *reviewed* field: ``target_user_id`` is
sent on signaling frames and deliberately ignored, because the media peer is
always resolved from the authenticated sender's own connection.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_PATH = REPO_ROOT / "ci-tooling" / "audit_failopen.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("audit_callback_delivery", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["audit_callback_delivery"] = module
    spec.loader.exec_module(module)
    return module


af = _load_tool()


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    """A server tree plus a client that sends one delivered and one not."""
    _write(
        tmp_path,
        "src/api/websocket/handlers/voice.py",
        """
from typing import Any, Dict


class VoiceHandler:
    async def handle_voice_speaking(self, connection, data: Dict[str, Any]):
        channel_id = data.get("channel_id")
        speaking = data.get("speaking", False)
        volume = data.get("volume", 0)
        return channel_id, speaking, volume

    async def handle_voice_quality(self, connection, data: Dict[str, Any]):
        quality = data.get("quality")
        channel_id = data["channel_id"]
        return quality, channel_id

    async def handle_voice_ice_candidate(self, connection, data: Dict[str, Any]):
        channel_id = data.get("channel_id")
        candidate = data.get("candidate")
        return channel_id, candidate, data.get("target_user_id")
""",
    )
    _write(
        tmp_path,
        "client/src/js/voice.js",
        """
const VoiceManager = {
    sendSpeaking(speaking, volume) {
        this._sendVoiceMessage(this.OPCODES.VOICE_SPEAKING, {
            channel_id: this.currentChannelId,
            speaking: speaking,
            volume: volume,
            acoustic_defense: enabled
        });
    },
    sendQuality(quality) {
        this._sendVoiceMessage(this.OPCODES.VOICE_QUALITY, {
            channel_id: this.currentChannelId,
            quality: quality,
            bogus_field: 1
        });
    },
    sendOffer(sdp) {
        this._sendVoiceMessage(this.OPCODES.VOICE_ICE_CANDIDATE, {
            channel_id: this.currentChannelId,
            target_user_id: peerId,
            candidate: sdp
        });
    }
};
""",
    )
    return tmp_path


def test_flags_a_field_no_handler_reads(tree: Path) -> None:
    hits, available = af.sweep_callback_delivery(tree, tree / "client")

    assert available is True
    assert [(h.kind, h.symbol) for h in hits] == [
        (af.UNDELIVERED, "VOICE_QUALITY.bogus_field")
    ]


def test_does_not_flag_a_field_the_handler_reads(tree: Path) -> None:
    hits, _available = af.sweep_callback_delivery(tree, tree / "client")

    symbols = {h.symbol for h in hits}
    assert "VOICE_SPEAKING.speaking" not in symbols
    assert "VOICE_SPEAKING.volume" not in symbols
    assert "VOICE_SPEAKING.channel_id" not in symbols
    assert "VOICE_QUALITY.quality" not in symbols


def test_does_not_flag_reviewed_informational_fields(tree: Path) -> None:
    """``target_user_id`` and ``acoustic_defense`` carry recorded verdicts."""
    hits, _available = af.sweep_callback_delivery(tree, tree / "client")

    symbols = {h.symbol for h in hits}
    assert "VOICE_ICE_CANDIDATE.target_user_id" not in symbols
    assert "VOICE_SPEAKING.acoustic_defense" not in symbols


def test_reads_both_get_and_subscript_access(tree: Path) -> None:
    """``data["channel_id"]`` counts as a read, not just ``data.get``."""
    hits, _available = af.sweep_callback_delivery(tree, tree / "client")

    assert "VOICE_QUALITY.channel_id" not in {h.symbol for h in hits}


def test_reports_the_client_tree_as_unavailable_not_clean(tmp_path: Path) -> None:
    """A run without the client checkout must not look like a pass."""
    _write(
        tmp_path,
        "src/api/websocket/handlers/voice.py",
        "class H:\n    async def handle_voice_speaking(self, connection, data):\n        return None\n",
    )

    hits, available = af.sweep_callback_delivery(tmp_path, tmp_path / "absent")

    assert hits == []
    assert available is False


def test_reports_a_missing_handler_file_as_unavailable(tree: Path) -> None:
    (tree / "src/api/websocket/handlers/voice.py").unlink()

    hits, available = af.sweep_callback_delivery(tree, tree / "client")

    assert hits == []
    assert available is False


def test_real_repository_delivers_every_media_callback() -> None:
    client_root = REPO_ROOT.parent / "plexichat-client"
    if not client_root.is_dir():
        pytest.skip("plexichat-client checkout not present")

    hits, available = af.sweep_callback_delivery(REPO_ROOT, client_root)

    assert available is True
    assert hits == [], [h.symbol for h in hits]
