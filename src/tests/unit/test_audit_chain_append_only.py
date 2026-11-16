"""Regression: the audit store is append-only and hash-chained.

A privileged-action audit trail that can be edited or silently truncated is
not an audit trail. These tests pin the guarantees of ``AuditChain``: rows are
only ever appended, each row commits to its predecessor, and the head sidecar
commits the row count/last checksum.
"""

import json

from src.core.telemetry.audit import GENESIS_HASH, AuditChain


def _chain(tmp_path):
    return AuditChain(
        str(tmp_path / "audit.jsonl"),
        head_path=str(tmp_path / "audit.jsonl.head"),
        durable=False,
    )


def test_empty_chain_is_valid(tmp_path):
    chain = _chain(tmp_path)
    valid, count, error = chain.verify_chain()
    assert (valid, count, error) == (True, 0, None)
    assert chain.last_checksum() == GENESIS_HASH
    assert chain.read_all() == []


def test_append_builds_a_hash_chain(tmp_path):
    chain = _chain(tmp_path)
    first = chain.append("user.ban", 42, target_id=7, details={"reason": "spam"})
    second = chain.append("role.assign", 42, target_id=7)

    assert first["seq"] == 1
    assert first["prev"] == GENESIS_HASH
    assert second["seq"] == 2
    assert second["prev"] == first["checksum"]
    assert chain.last_checksum() == second["checksum"]

    valid, count, error = chain.verify_chain()
    assert error is None
    assert valid is True
    assert count == 2


def test_appends_never_rewrite_existing_lines(tmp_path):
    chain = _chain(tmp_path)
    chain.append("user.ban", 42, target_id=1)
    before = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")

    chain.append("user.unban", 42, target_id=1)
    after = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")

    assert after.startswith(before), "appending must not rewrite earlier records"
    records = [json.loads(line) for line in after.splitlines() if line.strip()]
    assert [r["entry"]["seq"] for r in records] == [1, 2]


def test_read_all_returns_public_records(tmp_path):
    chain = _chain(tmp_path)
    chain.append("login", 99, details="ok")
    rows = chain.read_all()
    assert len(rows) == 1
    assert rows[0]["entry"]["action"] == "login"
    assert rows[0]["checksum"] == chain.last_checksum()
    assert rows[0]["entry"]["payload"]["details"] == "ok"


def test_store_exposes_no_mutation_api(tmp_path):
    chain = _chain(tmp_path)
    forbidden = {"delete", "remove", "update", "clear", "truncate", "purge"}
    assert not forbidden.intersection(dir(chain)), "audit store must be append-only"
