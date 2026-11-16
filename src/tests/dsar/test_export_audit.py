"""Tamper-evident audit entries for DSAR exports and deletions."""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest

pytestmark = pytest.mark.integration


def _entries(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _assert_integrity(entry):
    metadata = entry["metadata"]
    integrity = metadata["integrity_hash"]
    payload = {k: v for k, v in metadata.items() if k != "integrity_hash"}
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()
    assert integrity == expected


class TestIntegrityEvents:
    def test_export_event_has_integrity_hash_and_valid_chain(self, tmp_path):
        from src.core.dsar.audit_log import DSARLog

        path = tmp_path / "audit.jsonl"
        log = DSARLog(file_path=str(path))
        checksum = log.log_export_event(7, 123, "a" * 64, 42, "local", "dsar/x.json")

        entries = _entries(path)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["action"] == "EXPORTED"
        assert entry["checksum"] == checksum
        assert entry["metadata"]["artifact_sha256"] == "a" * 64
        assert entry["metadata"]["request_id"] == 123
        _assert_integrity(entry)
        assert log.verify_chain() == (True, 1, None)

    def test_deletion_event_records_outcome(self, tmp_path):
        from src.core.dsar.audit_log import DSARLog

        path = tmp_path / "audit.jsonl"
        log = DSARLog(file_path=str(path))
        log.log_deletion_event(7, 123, "local", "dsar/x.json", True)
        log.log_deletion_event(8, 124, "s3", "dsar/y.json", False)

        entries = _entries(path)
        assert [e["action"] for e in entries] == ["DELETED", "DELETED"]
        assert entries[0]["metadata"]["deleted"] is True
        assert entries[1]["metadata"]["deleted"] is False
        for entry in entries:
            _assert_integrity(entry)
        assert log.verify_chain()[0] is True


class TestManagerAuditWiring:
    @pytest.fixture
    def approved_request(self, db, auth_manager, pii_gen):
        from unittest.mock import patch

        from src.core.dsar import setup as dsar_setup
        from src.core.dsar import request_data_export
        from src.utils import encryption

        dsar_setup(db)
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username=f"audit_{uuid.uuid4().hex[:10]}",
                email=pii_gen.email(),
                password="TestPass123!",
            )
        req = request_data_export(user.id, format="json")
        db.execute(
            "UPDATE dsar_requests SET status = 'approved' WHERE id = ?", (req["id"],)
        )
        return db, req["id"], user.id

    def test_scrub_logs_deletion(self, approved_request, tmp_path, monkeypatch):
        from src.core.dsar.audit_log import DSARLog
        from src.core.dsar.manager import DSARManager

        db, req_id, user_id = approved_request
        artifact = tmp_path / "export.json"
        artifact.write_text('{"pii": true}')
        path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(
            DSARManager,
            "_get_audit_log",
            lambda self: DSARLog(file_path=str(path)),
        )
        row = dict(db.fetch_one("SELECT * FROM dsar_requests WHERE id = ?", (req_id,)))
        row["storage_path"] = str(artifact)
        row["storage_backend"] = "local"

        assert DSARManager(db)._scrub_export_files(row) is True
        assert not artifact.exists()
        entries = _entries(path)
        assert len(entries) == 1
        assert entries[0]["action"] == "DELETED"
        assert entries[0]["metadata"]["request_id"] == req_id
        assert entries[0]["user_id"] == user_id
        _assert_integrity(entries[0])

    def test_generate_logs_exported(self, approved_request, tmp_path, monkeypatch):
        from src.core.dsar.audit_log import DSARLog
        from src.core.dsar.manager import DSARManager

        db, req_id, user_id = approved_request
        path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(
            DSARManager,
            "_get_audit_log",
            lambda self: DSARLog(file_path=str(path)),
        )
        DSARManager(db).generate_manual(req_id, admin_id=1)

        actions = [e["action"] for e in _entries(path)]
        assert "READY" in actions
        assert "EXPORTED" in actions
        exported = next(e for e in _entries(path) if e["action"] == "EXPORTED")
        assert exported["user_id"] == user_id
        assert exported["metadata"]["request_id"] == req_id
        assert len(exported["metadata"]["artifact_sha256"]) == 64
        _assert_integrity(exported)
        assert DSARLog(file_path=str(path)).verify_chain()[0] is True
