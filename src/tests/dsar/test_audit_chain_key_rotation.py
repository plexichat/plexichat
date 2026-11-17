"""Regression: the DSAR audit hash chain stays verifiable across key rotation.

The chain may be authenticated with an internal HMAC key. Rotating that key
must not invalidate entries written with the previous one: every entry names
the key id that signed it, and all retired keys stay available for
verification. New entries use the new key. The log is never rewritten/reset.
"""

import json

import pytest

from src.core.dsar.audit_log import DSARLog


def _rows(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_rows(path, rows):
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_hash_chain_survives_key_rotation(tmp_path):
    path = tmp_path / "dsar_audit.jsonl"
    log = DSARLog(file_path=str(path), chain_key="old-secret", chain_key_id="k1")

    log.log_event(1, "EXPORTED", "dsar_request:1", {"n": 1})
    log.log_event(1, "EXPORTED", "dsar_request:2", {"n": 2})
    assert log.verify_chain() == (True, 2, None)

    log.rotate_chain_key("new-secret", "k2")
    log.log_event(2, "DELETED", "dsar_request:3", {"n": 3})
    log.log_event(2, "DELETED", "dsar_request:4", {"n": 4})

    assert log.verify_chain() == (True, 4, None)
    assert [row["key_id"] for row in _rows(path)] == ["k1", "k1", "k2", "k2"]

    # A fresh process with both keys persisted (as an operator would do)
    # verifies the whole chain, including the pre-rotation entries.
    restarted = DSARLog(
        file_path=str(path),
        chain_keys={"k1": "old-secret", "k2": "new-secret"},
        chain_key="new-secret",
        chain_key_id="k2",
    )
    assert restarted.verify_chain() == (True, 4, None)


def test_tampering_with_pre_rotation_entry_detected_after_rotation(tmp_path):
    path = tmp_path / "dsar_audit.jsonl"
    log = DSARLog(file_path=str(path), chain_key="old-secret", chain_key_id="k1")
    log.log_event(1, "EXPORTED", "dsar_request:1")
    log.rotate_chain_key("new-secret", "k2")
    log.log_event(2, "DELETED", "dsar_request:2")
    assert log.verify_chain() == (True, 2, None)

    rows = _rows(path)
    rows[0]["action"] = "TAMPERED"
    _write_rows(path, rows)

    valid, _count, error = log.verify_chain()
    assert valid is False
    assert "checksum invalid" in error


def test_missing_historical_key_fails_closed(tmp_path):
    path = tmp_path / "dsar_audit.jsonl"
    DSARLog(file_path=str(path), chain_key="only-secret", chain_key_id="k1").log_event(
        1, "EXPORTED", "dsar_request:1"
    )

    # A verifier that has forgotten the historical key must not silently fall
    # back to an unkeyed hash and accept the k1 entry.
    forgetful = DSARLog(file_path=str(path))
    valid, _count, error = forgetful.verify_chain()
    assert valid is False
    assert "unknown chain key id" in error


def test_legacy_unkeyed_entries_still_verify_with_keys_configured(tmp_path):
    path = tmp_path / "dsar_audit.jsonl"
    DSARLog(file_path=str(path)).log_event(1, "LEGACY", "dsar_request:1")

    keyed = DSARLog(file_path=str(path), chain_key="new-secret", chain_key_id="k2")
    assert keyed.verify_chain() == (True, 1, None)


def test_keyless_entry_appended_after_keyed_entry_is_rejected(tmp_path):
    """An attacker with write access to the log cannot downgrade an HMAC
    chain to the legacy unkeyed SHA-256 path by appending an entry without a
    ``key_id``. Such an entry's checksum is computable with only public data,
    so the verifier must treat "keyless after keyed" as a chain break.
    """
    path = tmp_path / "dsar_audit.jsonl"
    log = DSARLog(file_path=str(path), chain_key="secret-k1", chain_key_id="k1")
    log.log_event(1, "EXPORTED", "dsar_request:1")
    assert log.verify_chain() == (True, 1, None)

    rows = _rows(path)
    prev_hash = rows[-1]["checksum"]

    # Forge a keyless entry the way an attacker with file-write access would:
    # the unkeyed SHA-256 needs no secret because prev_hash is in the file.
    forged = {
        "timestamp": 2,
        "user_id": 1,
        "identifier_hash": "0" * 64,
        "action": "APPROVED",
        "metadata": {"request_id": 999, "admin_id": 1},
        "prev_hash": prev_hash,
    }
    content = json.dumps(forged, sort_keys=True)
    forged["checksum"] = __import__("hashlib").sha256(
        (prev_hash + content).encode("utf-8")
    ).hexdigest()
    _write_rows(path, rows + [forged])

    valid, count, error = log.verify_chain()
    assert valid is False
    assert count == 1
    assert "downgrade" in error


def test_rotate_chain_key_rejects_empty_values(tmp_path):
    log = DSARLog(file_path=str(tmp_path / "dsar_audit.jsonl"))
    with pytest.raises(ValueError):
        log.rotate_chain_key("", "k2")
    with pytest.raises(ValueError):
        log.rotate_chain_key("new-secret", "")
