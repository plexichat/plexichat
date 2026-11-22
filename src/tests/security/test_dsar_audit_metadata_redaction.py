"""Regression: DSAR audit writes must redact secrets in caller metadata.

``DSARLog.log_event`` persists a caller-supplied ``metadata`` dict verbatim to
the on-disk, hash-chained JSONL audit log. Real callers put free-form error
text there (``core/dsar/harvester.py`` logs ``{"error": str(e)}`` on a failed
export), so a credential embedded in an exception message would be written to
the audit file in the clear. The sibling write path ``AuditChain.append``
already scrubs before persisting; DSAR audit must do the same.

These tests are non-vacuous: they seed a token-like string, write through the
real ``log_event``/``log_export_event`` APIs and assert the secret is absent
from the persisted file while benign fields and the hash chain stay intact.
"""

import json

from src.core.dsar.audit_log import DSARLog
from src.core.telemetry.scrubbing import REDACTED

_SECRET = "s3cr3t-do-not-leak"


def test_log_event_redacts_error_metadata_before_persist(tmp_path):
    path = tmp_path / "dsar_audit.jsonl"
    log = DSARLog(file_path=str(path))

    log.log_event(
        7,
        "FAILED",
        "user:7",
        {"request_id": 7, "error": f"fetch failed token={_SECRET}"},
    )

    raw = path.read_text(encoding="utf-8")
    assert _SECRET not in raw
    entry = json.loads(raw.splitlines()[0])
    assert entry["metadata"]["request_id"] == 7
    assert _SECRET not in entry["metadata"]["error"]
    assert REDACTED in entry["metadata"]["error"]


def test_log_event_redacts_nested_secret_key_metadata(tmp_path):
    path = tmp_path / "dsar_audit.jsonl"
    log = DSARLog(file_path=str(path))

    log.log_event(
        1,
        "REQUESTED",
        "user:1",
        {"note": "ok", "nested": {"api_key": _SECRET, "count": 2}},
    )

    entry = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["metadata"]["note"] == "ok"
    assert entry["metadata"]["nested"]["api_key"] == REDACTED
    assert entry["metadata"]["nested"]["count"] == 2


def test_redacted_metadata_keeps_hash_chain_verifiable(tmp_path):
    path = tmp_path / "dsar_audit.jsonl"
    log = DSARLog(file_path=str(path), chain_key="k" * 32, chain_key_id="k1")

    log.log_event(1, "FAILED", "user:1", {"error": f"token={_SECRET}"})
    log.log_event(2, "RETRY", "user:2", {"error": f"token={_SECRET}"})

    assert _SECRET not in path.read_text(encoding="utf-8")
    valid, count, err = log.verify_chain()
    assert valid is True, err
    assert count == 2


def test_redacted_integrity_event_keeps_chain_verifiable(tmp_path):
    """Integrity-hash events must hash the *redacted* metadata, not the raw."""
    path = tmp_path / "dsar_audit.jsonl"
    log = DSARLog(file_path=str(path), chain_key="k" * 32, chain_key_id="k1")

    log.log_export_event(
        1,
        7,
        "a" * 64,
        123,
        "local",
        f"/exports/7?token={_SECRET}",
    )

    assert _SECRET not in path.read_text(encoding="utf-8")
    valid, count, err = log.verify_chain()
    assert valid is True, err
    assert count == 1
