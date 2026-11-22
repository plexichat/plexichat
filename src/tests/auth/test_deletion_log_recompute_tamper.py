"""Regression: rewriting deletion-log history by recomputing the chain is detectable.

The account-deletion audit chain used to commit to each row with an *unkeyed*
SHA-256 of ``prev_hash + json.dumps(entry, sort_keys=True)``. An actor who could
rewrite the JSONL file could edit any row and recompute every downstream
``prev_hash``/``checksum`` from public data alone, and ``verify_chain()`` still
returned a clean bill of health.

Every checksum is now an HMAC-SHA256 under a per-chain key, and every row names
the ``key_id`` that authenticated it (operator key or a ``0600`` local sidecar).
Verification fails closed on an unkeyed row, an unknown key id, or a missing key
for an already-populated chain.

The test reconstructs the old verifier to prove the forged chain really would
have passed before the fix -- a genuine tamper, not a vacuous assertion.
"""

import json
import hashlib
import stat

import pytest

from src.core.auth import deletion_log as dl

GENESIS = "0" * 64


def _rows(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_rows(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _old_verify(rows):
    """The pre-fix verifier: plain SHA-256, no key_id requirement."""
    expected_prev = GENESIS
    count = 0
    for row in rows:
        stored = row.get("checksum")
        entry = dict(row)
        entry.pop("checksum", None)
        if not dl._constant_time_hex_equal(entry.get("prev_hash"), expected_prev):
            return False
        content = json.dumps(entry, sort_keys=True)
        calculated = hashlib.sha256(
            (expected_prev + content).encode("utf-8")
        ).hexdigest()
        if not dl._constant_time_hex_equal(calculated, stored):
            return False
        expected_prev = stored
        count += 1
    return count > 0


def _recompute_unkeyed(rows):
    """Rewrite a whole chain with public data only (the old algorithm)."""
    prev = GENESIS
    for row in rows:
        row.pop("key_id", None)
        row.pop("checksum", None)
        row["prev_hash"] = prev
        content = json.dumps(row, sort_keys=True)
        row["checksum"] = hashlib.sha256((prev + content).encode("utf-8")).hexdigest()
        prev = row["checksum"]
    return prev


def test_clean_chain_is_keyed_and_verifies(tmp_path):
    path = tmp_path / "deletion.jsonl"
    log = dl.DeletionLog(str(path))
    for index in range(3):
        assert log.log_event(index, "SCHEDULED", f"user-{index}") is not None

    assert log.verify_chain() == (True, 3, None)
    assert log.get_scheduled_deletions().keys() == {0, 1, 2}

    rows = _rows(path)
    assert all(isinstance(row.get("key_id"), str) and row["key_id"] for row in rows)
    # Non-vacuity: a public-data SHA-256 does NOT reproduce a stored HMAC, so
    # a writer without the key cannot recompute this chain.
    first = dict(rows[0])
    content = json.dumps(first, sort_keys=True)
    assert (
        hashlib.sha256((first["prev_hash"] + content).encode("utf-8")).hexdigest()
        != rows[0]["checksum"]
    )


def test_recomputed_history_fails_closed(tmp_path):
    path = tmp_path / "deletion.jsonl"
    log = dl.DeletionLog(str(path))
    for index in range(3):
        log.log_event(index, "SCHEDULED", f"user-{index}")
    assert log.verify_chain()[0] is True  # sanity: start from a clean chain

    rows = _rows(path)
    rows[1]["metadata"] = {"FORGED": True}
    last_hash = _recompute_unkeyed(rows)
    _write_rows(path, rows)

    # Proof the forged chain is internally consistent and would have passed the
    # old unkeyed verifier -- i.e. the tamper is real, not a vacuous assertion.
    assert _old_verify(rows) is True
    assert rows[-1]["checksum"] == last_hash

    valid, count, error = log.verify_chain()
    assert valid is False
    assert error is not None
    assert count == 0  # caught at the first (now unkeyed) row
    assert "key" in (error or "").lower()


def test_local_key_is_0600_and_persists_across_restart(tmp_path):
    path = tmp_path / "deletion.jsonl"
    key_path = tmp_path / "deletion.jsonl.key"
    log = dl.DeletionLog(str(path))
    log.log_event(7, "SCHEDULED", "user-7")
    assert log.verify_chain() == (True, 1, None)

    assert key_path.exists()
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600

    # A fresh process must load the same local key, not mint a new one.
    restarted = dl.DeletionLog(str(path))
    assert restarted.verify_chain() == (True, 1, None)
    restarted.log_event(8, "CANCELLED", "user-8")
    assert restarted.verify_chain() == (True, 2, None)


def test_missing_key_for_nonempty_chain_fails_closed(tmp_path):
    path = tmp_path / "deletion.jsonl"
    key_path = tmp_path / "deletion.jsonl.key"
    log = dl.DeletionLog(str(path))
    log.log_event(1, "SCHEDULED", "user-1")
    assert log.verify_chain() == (True, 1, None)

    key_path.unlink()
    forgetful = dl.DeletionLog(str(path))
    valid, _count, error = forgetful.verify_chain()
    assert valid is False
    assert error is not None
    assert "key" in error.lower()
    # Appends must also refuse to write with a key we do not hold.
    assert forgetful.log_event(2, "SCHEDULED", "user-2") is None


def test_operator_key_rotation_keeps_old_rows_verifiable(tmp_path):
    path = tmp_path / "deletion.jsonl"
    log = dl.DeletionLog(str(path), chain_key="old-secret", chain_key_id="k1")
    for index in range(2):
        log.log_event(index, "SCHEDULED", f"user-{index}")
    assert log.verify_chain() == (True, 2, None)

    log.rotate_chain_key("new-secret", "k2")
    for index in range(2, 4):
        log.log_event(index, "SCHEDULED", f"user-{index}")
    assert log.verify_chain() == (True, 4, None)
    assert [row["key_id"] for row in _rows(path)] == ["k1", "k1", "k2", "k2"]

    restarted = dl.DeletionLog(
        str(path),
        chain_keys={"k1": "old-secret", "k2": "new-secret"},
        chain_key="new-secret",
        chain_key_id="k2",
    )
    assert restarted.verify_chain() == (True, 4, None)

    # A verifier that forgot the retired key must not silently fall back to an
    # unkeyed hash for the k1 rows.
    forgetful = dl.DeletionLog(str(path), chain_key="new-secret", chain_key_id="k2")
    valid, _count, error = forgetful.verify_chain()
    assert valid is False
    assert "unknown chain key id" in (error or "")


def test_unkeyed_row_appended_after_keyed_row_is_rejected(tmp_path):
    path = tmp_path / "deletion.jsonl"
    log = dl.DeletionLog(str(path))
    log.log_event(1, "SCHEDULED", "user-1")
    assert log.verify_chain() == (True, 1, None)

    rows = _rows(path)
    prev_hash = rows[-1]["checksum"]
    forged = {
        "timestamp": 2,
        "user_id": 1,
        "identifier_hash": "0" * 64,
        "action": "FORCE_PURGED",
        "metadata": {},
        "prev_hash": prev_hash,
    }
    content = json.dumps(forged, sort_keys=True)
    forged["checksum"] = hashlib.sha256(
        (prev_hash + content).encode("utf-8")
    ).hexdigest()
    _write_rows(path, rows + [forged])

    # The forgery is a correctly formed unkeyed row (public data only), so a
    # verifier with a "legacy keyless" fallback would accept it. The fixed
    # verifier must reject the downgrade instead.
    assert prev_hash == rows[-1]["checksum"]
    valid, count, error = log.verify_chain()
    assert valid is False
    assert count == 1
    assert "key" in (error or "").lower()


def test_legacy_unkeyed_chain_fails_closed(tmp_path):
    path = tmp_path / "deletion.jsonl"
    rows = []
    prev = GENESIS
    for index in range(2):
        row = {
            "timestamp": index,
            "user_id": index,
            "identifier_hash": "0" * 64,
            "action": "SCHEDULED",
            "metadata": {},
            "prev_hash": prev,
        }
        row["checksum"] = hashlib.sha256(
            (prev + json.dumps(row, sort_keys=True)).encode("utf-8")
        ).hexdigest()
        rows.append(row)
        prev = row["checksum"]
    _write_rows(path, rows)

    assert _old_verify(rows) is True  # it is a valid *legacy* chain
    log = dl.DeletionLog(str(path))
    valid, _count, error = log.verify_chain()
    assert valid is False
    assert error is not None
    assert "key" in error.lower()


def test_rotate_chain_key_rejects_empty_values(tmp_path):
    log = dl.DeletionLog(str(tmp_path / "deletion.jsonl"))
    with pytest.raises(ValueError):
        log.rotate_chain_key("", "k2")
    with pytest.raises(ValueError):
        log.rotate_chain_key("new-secret", "")
