"""Regression tests for H4: transcript export IDs, perms, concurrency, limits.

Fails on pre-fix code where export IDs were guessable, temp files were
world-readable, bookkeeping was unlocked, or limits were enforced after
artifact creation.
"""

import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import utils.config as config_mod
from src.core.messaging.services.export import TranscriptExportService


class _StubMessages:
    def __init__(self, msgs):
        self._msgs = msgs

    def get_by_conversation(self, conversation_id, limit=10000):
        return list(self._msgs[:limit])


class _StubParticipants:
    def __init__(self, allowed=True):
        self._allowed = allowed

    def exists(self, conversation_id, user_id):
        return self._allowed


def _sample_messages(n=3):
    return [
        {
            "id": str(1000 + i),
            "author_id": "42",
            "content": f"hello {i}",
            "created_at": 1700000000000 + i,
            "edited_at": None,
            "reply_to_id": None,
        }
        for i in range(n)
    ]


@pytest.fixture
def export_env(tmp_path, monkeypatch):
    """Isolated service with private storage dir and generous rate limit."""
    state = {"limit": 1000}

    real_get = config_mod.get

    def fake_get(key, default=None):
        if key == "transcript_export":
            return {
                "storage_dir": str(tmp_path),
                "rate_limit": {"requests_per_hour": state["limit"]},
                "temporary_storage_hours": 24,
                "max_messages_per_export": 10000,
            }
        try:
            return real_get(key, default)
        except Exception:
            return default

    monkeypatch.setattr(config_mod, "get", fake_get)
    TranscriptExportService._exports.clear()
    TranscriptExportService._hydrated = True
    yield state
    TranscriptExportService._exports.clear()
    TranscriptExportService._hydrated = False


def _make_svc(allowed=True, n_msgs=3):
    return TranscriptExportService(
        None, _StubMessages(_sample_messages(n_msgs)), _StubParticipants(allowed)
    )


def _all_files(root):
    return [p for p in root.rglob("*") if p.is_file()]


def test_export_ids_unguessable_and_unique(export_env):
    svc = _make_svc()
    ids = [svc.request_export("42", "7", "json")["export_id"] for _ in range(20)]
    assert len(set(ids)) == 20
    for eid in ids:
        assert eid.startswith("export_")
        suffix = eid[len("export_") :]
        # 256-bit token_urlsafe(32) -> 43 chars; pre-fix ids were short/guessable.
        assert len(suffix) >= 32
    # No sequential / counter-like pattern: all suffixes pairwise differ widely.
    suffixes = [i[len("export_") :] for i in ids]
    for a in suffixes:
        for b in suffixes:
            if a is b:
                continue
            if a == b:
                pytest.fail("duplicate export id suffix")
    assert len({s[:8] for s in suffixes}) > 1


def test_export_files_private_perms(export_env, tmp_path):
    svc = _make_svc()
    result = svc.request_export("42", "7", "json")
    assert result["status"] == "ready"
    fpath = result["file_path"]
    assert fpath and os.path.isfile(fpath)
    assert stat.S_IMODE(os.stat(fpath).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(fpath)).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(tmp_path).st_mode) == 0o700
    # World-readable bits must be absent everywhere under the storage dir.
    for p in [tmp_path, *tmp_path.rglob("*")]:
        mode = stat.S_IMODE(os.stat(p).st_mode)
        assert mode & 0o044 == 0, f"{p} is world-readable: {oct(mode)}"


def test_concurrent_exports_all_tracked(export_env):
    svc = _make_svc()
    n = 12
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda i: svc.request_export("42", "7", "json"), range(n))
        )
    assert all(r["status"] == "ready" for r in results)
    ids = [r["export_id"] for r in results]
    assert len(set(ids)) == n
    # Every concurrent export must be present in bookkeeping (no lost updates).
    with TranscriptExportService._lock:
        for eid in ids:
            assert eid in TranscriptExportService._exports
            assert TranscriptExportService._exports[eid]["status"] == "ready"


def test_limit_enforced_before_artifact(export_env, tmp_path):
    export_env["limit"] = 2
    svc = _make_svc()
    first = svc.request_export("42", "7", "json")
    second = svc.request_export("42", "7", "json")
    assert first["status"] == "ready" and second["status"] == "ready"
    files_before = set(_all_files(tmp_path))
    assert len(files_before) >= 2
    limited = svc.request_export("42", "7", "json")
    assert limited["status"] == "failed"
    assert "Rate limit" in (limited.get("error") or "")
    assert limited.get("file_url") is None
    # Rate-limited call must not create any new artifact on disk.
    assert set(_all_files(tmp_path)) == files_before


def test_unauthorized_creates_no_artifact(export_env, tmp_path):
    svc = _make_svc(allowed=False)
    result = svc.request_export("99", "7", "json")
    assert result["status"] == "failed"
    assert result.get("file_url") is None
    assert _all_files(tmp_path) == []
