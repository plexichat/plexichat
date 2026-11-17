"""Regression: any modification or deletion of audit rows is detectable.

These tests attack the stored record file directly -- changing a payload,
removing a middle row, truncating the tail, reordering rows, or forging the
head -- and require ``verify_chain()`` to fail (and ``assert_intact()`` to
raise). A clean chain must still pass.
"""

import json

import pytest

from src.core.telemetry.audit import AuditChain, AuditChainError


def _chain(tmp_path, count=3):
    chain = AuditChain(
        str(tmp_path / "audit.jsonl"),
        head_path=str(tmp_path / "audit.jsonl.head"),
        durable=False,
    )
    for index in range(count):
        chain.append(f"user.action_{index}", 42, details=f"entry-{index}")
    return chain


def _records(tmp_path):
    return [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text().splitlines()
        if line.strip()
    ]


def _write(tmp_path, records):
    (tmp_path / "audit.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )


def test_clean_chain_passes_assert_intact(tmp_path):
    chain = _chain(tmp_path)
    assert chain.assert_intact() == 3


def test_modified_payload_fails_verification(tmp_path):
    chain = _chain(tmp_path)
    records = _records(tmp_path)
    records[1]["entry"]["payload"]["details"] = "forged-entry"
    _write(tmp_path, records)

    valid, _, error = chain.verify_chain()
    assert valid is False
    assert error is not None
    assert "checksum" in error
    with pytest.raises(AuditChainError):
        chain.assert_intact()


def test_deleted_middle_entry_fails_verification(tmp_path):
    chain = _chain(tmp_path)
    records = _records(tmp_path)
    _write(tmp_path, [records[0], records[2]])

    valid, _, error = chain.verify_chain()
    assert valid is False
    assert error is not None
    assert "sequence" in error or "prev_hash" in error
    with pytest.raises(AuditChainError):
        chain.assert_intact()


def test_deleted_tail_entry_fails_verification(tmp_path):
    chain = _chain(tmp_path)
    records = _records(tmp_path)
    _write(tmp_path, records[:-1])

    valid, count, error = chain.verify_chain()
    assert valid is False
    assert count == 2
    assert error is not None
    assert "head" in error or "count" in error


def test_reordered_entries_fail_verification(tmp_path):
    chain = _chain(tmp_path)
    records = _records(tmp_path)
    _write(tmp_path, [records[1], records[0], records[2]])

    valid, _, error = chain.verify_chain()
    assert valid is False
    assert error is not None


def test_forged_head_fails_verification(tmp_path):
    chain = _chain(tmp_path)
    head_path = tmp_path / "audit.jsonl.head"
    head = json.loads(head_path.read_text())
    head["last_hash"] = "f" * 64
    head_path.write_text(json.dumps(head) + "\n", encoding="utf-8")

    valid, _, error = chain.verify_chain()
    assert valid is False
    assert error is not None
    assert "head" in error
