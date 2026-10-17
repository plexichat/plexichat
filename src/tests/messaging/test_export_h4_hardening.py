"""H4 follow-up hardening regression tests (Agent 13).

Proves the residual fixes on top of test_export_ids_m4.py:
- 256-bit IDs (token_urlsafe(32) -> 43-char suffix), unique, no fallback shape
- transcript file O_CREAT|O_EXCL 0600 (second exclusive create fails)
- record files 0600, no predictable ``.tmp`` leftovers
- malformed/traversal export_ids rejected by status + file-path getters
- auth failure stores NO bookkeeping entry and consumes NO rate budget
- concurrent burst respects the rate limit with zero lost/duplicate entries
"""

import os
import stat
from concurrent.futures import ThreadPoolExecutor

import pytest

import utils.config as config_mod
from src.core.messaging.services.export import TranscriptExportService


class _StubMessages:
    def __init__(self, msgs):
        self._msgs = msgs

    def get_by_conversation(
        self, conversation_id, limit=10000, from_ts=None, to_ts=None, ascending=False
    ):
        return list(self._msgs[:limit])


class _StubParticipants:
    def __init__(self, allowed=True):
        self._allowed = allowed

    def exists(self, conversation_id, user_id):
        return self._allowed


def _sample_messages(n=2):
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


def _make_svc(allowed=True, n_msgs=2):
    return TranscriptExportService(
        None, _StubMessages(_sample_messages(n_msgs)), _StubParticipants(allowed)
    )


def test_ids_are_256bit_unique(export_env):
    svc = _make_svc()
    ids = [svc.request_export("42", "7", "json")["export_id"] for _ in range(10)]
    assert len(set(ids)) == 10
    for eid in ids:
        assert eid.startswith("export_")
        suffix = eid[len("export_") :]
        # secrets.token_urlsafe(32) -> 43 base64url chars (256 bits).
        assert len(suffix) == 43, f"weak export id: {eid!r}"
        assert all(c.isalnum() or c in "-_" for c in suffix)


def test_exclusive_create_no_overwrite_window(export_env):
    svc = _make_svc()
    result = svc.request_export("42", "7", "json")
    assert result["status"] == "ready"
    fpath = result["file_path"]
    assert stat.S_IMODE(os.stat(fpath).st_mode) == 0o600
    # A second O_EXCL create at the same path must fail closed, never
    # truncate/replace the live transcript.
    with pytest.raises(FileExistsError):
        TranscriptExportService._write_private_file(fpath, "pwned")
    with open(fpath, encoding="utf-8") as f:
        assert "pwned" not in f.read()


def test_record_files_private_no_predictable_tmp(export_env, tmp_path):
    svc = _make_svc()
    result = svc.request_export("42", "7", "json")
    assert result["status"] == "ready"
    records = list((tmp_path / "records").glob("*.json"))
    assert len(records) == 1
    assert stat.S_IMODE(os.stat(records[0]).st_mode) == 0o600
    # No predictable "<id>.json.tmp" artifacts may remain.
    assert list((tmp_path / "records").glob("*.tmp")) == []
    assert list((tmp_path / "records").glob(".tmp-*")) == []


def test_malformed_ids_rejected(export_env):
    svc = _make_svc()
    for bad in [
        "../secret",
        "export_../../etc/passwd",
        "export_short",
        "",
        "../../x",
        "export_a/b",
        "plaintext",
    ]:
        assert svc.get_export_status(bad) is None
        assert svc.get_export_file_path(bad, "42") is None


def test_auth_failure_tracks_nothing_consumes_nothing(export_env, tmp_path):
    export_env["limit"] = 2
    bad = _make_svc(allowed=False)
    r1 = bad.request_export("99", "7", "json")
    r2 = bad.request_export("99", "7", "json")
    assert r1["status"] == "failed" and r2["status"] == "failed"
    # No bookkeeping entries, no disk artifacts, no rate budget consumed.
    assert TranscriptExportService._exports == {}
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []
    good = _make_svc(allowed=True)
    ok1 = good.request_export("99", "7", "json")
    ok2 = good.request_export("99", "7", "json")
    assert ok1["status"] == "ready" and ok2["status"] == "ready"


def test_concurrent_burst_atomic_limit(export_env):
    export_env["limit"] = 5
    svc = _make_svc()
    n = 20
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(
            pool.map(lambda i: svc.request_export("42", "7", "json"), range(n))
        )
    ready = [r for r in results if r["status"] == "ready"]
    limited = [r for r in results if r["status"] == "failed"]
    assert len(ready) == 5
    assert all("Rate limit" in (r.get("error") or "") for r in limited)
    assert len({r["export_id"] for r in results}) == n
    with TranscriptExportService._lock:
        assert len(TranscriptExportService._exports) == 5
        assert all(
            v["status"] == "ready" for v in TranscriptExportService._exports.values()
        )
