"""Compatibility and package-boundary tests for the mediasoup WebSocket adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.voice.signaling.sfu import mediasoup_ws as pkg
from src.core.voice.signaling.sfu.base import SFUAdapter
from src.core.voice.signaling.sfu.mediasoup_ws import (
    MediasoupWSAdapter,
    PeerConnection,
    PendingRequest,
    _load_stable_peer_secret,
    _stable_peer_id,
    ensure_within_recording_dir,
    resolve_recording_dir,
    safe_filename_component,
)


class TestCompatSurface:
    def test_package_exports_match_submodules(self) -> None:
        assert pkg.MediasoupWSAdapter is MediasoupWSAdapter
        assert pkg.PeerConnection is PeerConnection
        assert pkg.PendingRequest is PendingRequest
        assert pkg.ensure_within_recording_dir is ensure_within_recording_dir
        assert pkg.resolve_recording_dir is resolve_recording_dir
        assert pkg.safe_filename_component is safe_filename_component
        assert pkg._stable_peer_id is _stable_peer_id
        assert pkg._load_stable_peer_secret is _load_stable_peer_secret

    def test_sfu_package_reexport_is_the_same_class(self) -> None:
        from src.core.voice.signaling.sfu import MediasoupWSAdapter as exported

        assert exported is MediasoupWSAdapter

    def test_every_public_name_resolves(self) -> None:
        for name in pkg.__all__:
            assert hasattr(pkg, name), name

    def test_adapter_is_composed_from_the_responsibility_mixins(self) -> None:
        assert issubclass(MediasoupWSAdapter, SFUAdapter)
        for method in (
            "_connect",
            "_request",
            "create_room",
            "create_transport",
            "start_recording",
            "stop_recording",
            "close",
        ):
            assert callable(getattr(MediasoupWSAdapter, method)), method

    def test_stable_peer_secret_is_live_not_frozen(self, monkeypatch) -> None:
        monkeypatch.setattr(pkg._identity, "_STABLE_PEER_SECRET", b"rotated")
        assert pkg._STABLE_PEER_SECRET == b"rotated"

    def test_missing_attribute_still_raises(self) -> None:
        with pytest.raises(AttributeError):
            _ = pkg.does_not_exist


class TestRecordingGuardPatchPoint:
    @pytest.mark.asyncio
    async def test_stop_recording_resolves_the_package_level_guard(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """The documented monkeypatch point must reach the recording mixin."""
        adapter = MediasoupWSAdapter.__new__(MediasoupWSAdapter)
        adapter._connections = {}
        adapter._recordings = {
            "room-1": {
                "recording_id": "rec_1",
                "output_dir": str(tmp_path),
                "file_count": 1,
                "transport_id": None,
                "consumer_ids": [],
                "recorder_peer_id": None,
            }
        }
        seen: list[tuple[str, str]] = []

        def _guard(path, root):
            seen.append((str(path), str(root)))
            return Path(path)

        monkeypatch.setattr(pkg, "ensure_within_recording_dir", _guard)

        paths = await adapter.stop_recording("room-1")

        assert seen, "recording did not use the package-level path guard"
        assert paths
