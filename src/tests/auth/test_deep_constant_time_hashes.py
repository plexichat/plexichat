"""Deep hardening: constant-time comparison of audit-log integrity values.

The deletion audit log is a tamper-evident SHA-256 chain. Its ``prev_hash``
links and per-record ``checksum`` are integrity secrets: comparing them with
``==``/``!=`` leaks a prefix oracle through early-exit timing. These tests
pin the verifier to :func:`hmac.compare_digest` and to fail-closed handling of
malformed digests.
"""

import hashlib
import json

from src.core.auth import deletion_log as dl


def test_hex_equal_uses_compare_digest(monkeypatch):
    calls = []
    real = dl.hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(dl.hmac, "compare_digest", spy)
    digest = hashlib.sha256(b"x").hexdigest()
    assert dl._constant_time_hex_equal(digest, digest) is True
    assert len(calls) == 1


def test_hex_equal_fails_closed_on_malformed_values():
    digest = hashlib.sha256(b"x").hexdigest()
    assert dl._constant_time_hex_equal(digest, digest) is True
    assert dl._constant_time_hex_equal(digest, "0" * 64) is False
    # Missing / non-string / truncated digests never raise and never match.
    assert dl._constant_time_hex_equal(None, digest) is False
    assert dl._constant_time_hex_equal(digest, None) is False
    assert dl._constant_time_hex_equal(123, digest) is False
    assert dl._constant_time_hex_equal(digest[:63], digest) is False


def test_chain_valid_and_tamper_detected(tmp_path):
    log = dl.DeletionLog(str(tmp_path / "deletion.jsonl"))
    assert log.log_event(1, "SCHEDULED", "user-1") is not None
    assert log.log_event(2, "CANCELLED", "user-2") is not None
    valid, count, err = log.verify_chain()
    assert (valid, count, err) == (True, 2, None)

    lines = (tmp_path / "deletion.jsonl").read_text().splitlines()
    assert len(lines) == 2
    records = [json.loads(line) for line in lines]
    # Tamper with the second record's metadata without recomputing checksum.
    records[1]["metadata"] = {"tampered": True}
    (tmp_path / "deletion.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n"
    )
    valid, count, err = log.verify_chain()
    assert valid is False
    assert "checksum invalid" in (err or "")


def test_chain_verification_routes_checksum_through_constant_time(
    monkeypatch, tmp_path
):
    log = dl.DeletionLog(str(tmp_path / "deletion.jsonl"))
    log.log_event(7, "SCHEDULED", "user-7")
    real = dl.hmac.compare_digest
    calls = []

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(dl.hmac, "compare_digest", spy)
    valid, _, _ = log.verify_chain()
    assert valid is True
    # Two comparisons per record: prev_hash link and checksum.
    assert len(calls) >= 2
