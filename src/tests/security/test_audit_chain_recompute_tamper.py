"""Regression: rewriting audit history by recomputing the chain is detectable.

The telemetry audit chain used to commit to each row with an *unkeyed*
SHA-256. An actor who could rewrite the JSONL file and its head sidecar could
edit a row, recompute every downstream ``prev``/``checksum`` from public data
alone, and ``verify_chain()`` returned a clean bill of health. Rows are now
HMAC-authenticated, so the same consistent rewrite must fail closed.

The test reconstructs the old verifier to prove the forged chain really would
have passed before the fix -- it is a genuine tamper, not a vacuous assertion.
"""

import hashlib
import json

import pytest

from src.core.telemetry.audit import GENESIS_HASH, AuditChain, AuditChainError


def _canon(entry):
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _chain(tmp_path):
    return AuditChain(
        str(tmp_path / "audit.jsonl"),
        head_path=str(tmp_path / "audit.jsonl.head"),
        durable=False,
    )


def _records(tmp_path):
    return [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write(tmp_path, records, last_hash):
    (tmp_path / "audit.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    (tmp_path / "audit.jsonl.head").write_text(
        json.dumps({"count": len(records), "last_hash": last_hash}) + "\n",
        encoding="utf-8",
    )


def _recompute_unkeyed(records, *, strip_key_id):
    """Recompute a whole chain with public data only (the old algorithm)."""
    prev = GENESIS_HASH
    for record in records:
        if strip_key_id:
            record.pop("key_id", None)
        record["prev"] = prev
        record["checksum"] = hashlib.sha256(
            (prev + _canon(record["entry"])).encode("utf-8")
        ).hexdigest()
        prev = record["checksum"]
    return prev


def _old_verify(records, head):
    """The pre-fix verifier, to prove the rewrite is a real, complete tamper."""
    expected_prev = GENESIS_HASH
    count = 0
    for record in records:
        entry = record.get("entry") or {}
        if entry.get("seq") != count + 1:
            return False
        if record.get("prev") != expected_prev:
            return False
        calculated = hashlib.sha256(
            (expected_prev + _canon(entry)).encode("utf-8")
        ).hexdigest()
        if calculated != record.get("checksum"):
            return False
        expected_prev = record["checksum"]
        count += 1
    return count == head["count"] and expected_prev == head["last_hash"]


def test_clean_chain_is_keyed_and_verifies(tmp_path):
    chain = _chain(tmp_path)
    for index in range(3):
        chain.append(f"user.action_{index}", 42, details=f"entry-{index}")

    assert chain.verify_chain() == (True, 3, None)
    assert chain.assert_intact() == 3

    records = _records(tmp_path)
    # Non-vacuous: a public-data SHA-256 does NOT reproduce the stored checksum,
    # so a writer without the HMAC key cannot recompute this chain.
    assert all(isinstance(row.get("key_id"), str) and row["key_id"] for row in records)
    assert (
        hashlib.sha256(
            (records[0]["prev"] + _canon(records[0]["entry"])).encode("utf-8")
        ).hexdigest()
        != records[0]["checksum"]
    )


@pytest.mark.parametrize("strip_key_id", [False, True])
def test_recomputed_history_fails_closed(tmp_path, strip_key_id):
    chain = _chain(tmp_path)
    for index in range(3):
        chain.append(f"user.action_{index}", 42, details=f"entry-{index}")
    assert chain.verify_chain()[0] is True  # sanity: start from a clean chain

    records = _records(tmp_path)
    records[1]["entry"]["payload"]["details"] = "FORGED"
    last_hash = _recompute_unkeyed(records, strip_key_id=strip_key_id)
    _write(tmp_path, records, last_hash)

    head = json.loads((tmp_path / "audit.jsonl.head").read_text(encoding="utf-8"))
    # Proof the forged chain is internally consistent and would have passed the
    # old unkeyed verifier -- i.e. the tamper is real, not a vacuous assertion.
    assert _old_verify(records, head) is True

    valid, count, error = chain.verify_chain()
    assert valid is False
    assert error is not None
    assert count == 0  # caught at the first (recomputed) row
    with pytest.raises(AuditChainError):
        chain.assert_intact()


def test_key_persists_across_restart_and_missing_key_fails_closed(tmp_path):
    chain = _chain(tmp_path)
    chain.append("user.ban", 42)
    assert chain.verify_chain() == (True, 1, None)

    # A fresh process must load the same local key, not mint a new one.
    restarted = _chain(tmp_path)
    assert restarted.verify_chain() == (True, 1, None)
    restarted.append("user.unban", 42)
    assert restarted.verify_chain() == (True, 2, None)

    # Losing the key of a non-empty chain must fail closed, never reset it.
    (tmp_path / "audit.jsonl.head.key").unlink()
    forgetful = _chain(tmp_path)
    valid, _count, error = forgetful.verify_chain()
    assert valid is False
    assert error is not None
    assert "key" in error.lower()
