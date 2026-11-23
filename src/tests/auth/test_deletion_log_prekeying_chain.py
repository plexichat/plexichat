"""Regression: a pre-keying deletion chain must not block every append.

The keyed HMAC chain landed after deployments already held a plain SHA-256
chain. Those legacy rows carry no ``key_id``, so no key can authenticate them;
the fail-closed loader treated the whole file as unverifiable and refused every
append. Account deletions kept returning success while their audit rows were
dropped -- the gap only showed up as a production warning.

A legacy chain is therefore preserved aside exactly once and a keyed chain
starts in its place. A keyed chain whose key was lost, a row that names a key,
and an unparseable file all keep failing closed: those mean lost evidence or
tampering, not an upgrade.
"""

import json
import stat

import pytest

from src.core.auth import deletion_log as dl


def _legacy_row(prev_hash, user_id):
    """A row exactly as the plain (unkeyed) chain wrote it: no ``key_id``."""
    entry = {
        "timestamp": 1793126949,
        "user_id": user_id,
        "identifier_hash": "a" * 64,
        "action": "SCHEDULED",
        "metadata": {"deletion_at": 1795718949849},
    }
    entry["prev_hash"] = prev_hash
    entry["checksum"] = "b" * 64
    return entry


def _write_legacy_chain(path, rows=2):
    prev = "0" * 64
    lines = []
    for i in range(rows):
        entry = _legacy_row(prev, user_id=i + 1)
        lines.append(json.dumps(entry))
        prev = entry["checksum"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path.read_text(encoding="utf-8")


def _keyed_log_without_sidecar(tmp_path):
    """Build a real keyed chain, then drop its key sidecar."""
    log = dl.DeletionLog(file_path=str(tmp_path / "deletion.jsonl"))
    assert log.log_event(user_id=7, action="account_deleted", identifier="user-7"), (
        "fixture chain did not accept its first row"
    )
    key_path = tmp_path / "deletion.jsonl.key"
    assert key_path.exists()
    key_path.unlink()
    return tmp_path / "deletion.jsonl"


def test_legacy_chain_is_preserved_and_keyed_appends_resume(tmp_path):
    log_path = tmp_path / "deletion.jsonl"
    legacy_bytes = _write_legacy_chain(log_path)

    log = dl.DeletionLog(file_path=str(log_path))
    assert log._key_error is None, log._key_error
    checksum = log.log_event(user_id=99, action="account_deleted", identifier="user-99")
    assert checksum, "an append against an upgraded legacy chain was refused"

    # The old evidence is still on disk, byte for byte, under a name an
    # operator can find; nothing was truncated or rewritten.
    preserved = sorted(tmp_path.glob("deletion.jsonl.pre-key-*"))
    assert len(preserved) == 1, preserved
    assert preserved[0].read_text(encoding="utf-8") == legacy_bytes

    # The live chain restarted with a key that is recorded per row.
    rows = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    assert rows[0]["key_id"] == dl._LOCAL_KEY_ID
    assert rows[0]["prev_hash"] == "0" * 64

    key_path = tmp_path / "deletion.jsonl.key"
    assert key_path.exists()
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600

    valid, count, error = dl.DeletionLog(file_path=str(log_path)).verify_chain()
    assert (valid, count, error) == (True, 1, None)


def test_keyed_chain_without_its_key_still_fails_closed(tmp_path):
    log_path = _keyed_log_without_sidecar(tmp_path)
    before = log_path.read_bytes()

    log = dl.DeletionLog(file_path=str(log_path))
    assert (
        log.log_event(user_id=8, action="account_deleted", identifier="user-8") is None
    )

    valid, count, error = log.verify_chain()
    assert valid is False
    assert count == 0
    assert error == "deletion log chain key sidecar is missing for a non-empty chain"

    # No new key was minted, nothing was moved, and the rows are untouched.
    assert not (tmp_path / "deletion.jsonl.key").exists()
    assert not list(tmp_path.glob("deletion.jsonl.pre-key-*"))
    assert log_path.read_bytes() == before


@pytest.mark.parametrize(
    "payload", ["{not json}\n", '{"user_id": 1, "key_id": "local"}\n']
)
def test_unparseable_or_keyed_chain_is_never_adopted(tmp_path, payload):
    log_path = tmp_path / "deletion.jsonl"
    log_path.write_text(payload, encoding="utf-8")

    log = dl.DeletionLog(file_path=str(log_path))
    assert (
        log._key_error
        == "deletion log chain key sidecar is missing for a non-empty chain"
    )

    assert not list(tmp_path.glob("deletion.jsonl.pre-key-*"))
    assert not (tmp_path / "deletion.jsonl.key").exists()
    assert log_path.read_text(encoding="utf-8") == payload
