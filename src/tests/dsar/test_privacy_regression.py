"""Consolidated privacy regression suite for DSAR exports.

Three end-to-end invariants, verified against the artifact actually written
to storage rather than only the in-memory collector payload:

1. cross-user  - an export contains only the requesting subject's data;
2. PII          - third-party identifiers are redacted, in fields and in text;
3. oversize     - a too-large export fails closed and leaves no artifact.
"""

from __future__ import annotations

import json
import time
import uuid

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def privacy_env(db, auth_manager, pii_gen, tmp_path, monkeypatch):
    from unittest.mock import patch

    from src.core.dsar import setup as dsar_setup
    from src.core.dsar import request_data_export
    from src.core.dsar.audit_log import DSARLog
    from src.core.dsar.manager import DSARManager
    from src.utils import encryption

    dsar_setup(db)
    monkeypatch.setattr(
        DSARManager,
        "_get_audit_log",
        lambda self: DSARLog(file_path=str(tmp_path / "audit.jsonl")),
    )
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        subject = auth_manager.register(
            username=f"subj_{uuid.uuid4().hex[:10]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )
        other = auth_manager.register(
            username=f"other_{uuid.uuid4().hex[:10]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )
    return {
        "db": db,
        "subject": subject,
        "other": other,
        "request_export": request_data_export,
    }


def _approve_and_generate(env):
    from src.core.dsar.manager import DSARManager

    db = env["db"]
    req = env["request_export"](env["subject"].id, format="json")
    db.execute(
        "UPDATE dsar_requests SET status = 'approved' WHERE id = ?", (req["id"],)
    )
    request = DSARManager(db).generate_manual(req["id"], admin_id=1)
    from src.core.dsar.export_formats import ExportFormatGenerator

    artifact = ExportFormatGenerator(db=db).retrieve(request["storage_path"])
    return req["id"], request, json.loads(artifact)


class TestCrossUserRegression:
    def test_other_users_rows_never_reach_export(self, privacy_env):
        db = privacy_env["db"]
        subject = privacy_env["subject"]
        other = privacy_env["other"]
        now = int(time.time())
        other_secret = f"OTHER_SECRET_{uuid.uuid4().hex}"
        other_token = f"OTHER_TOKEN_{uuid.uuid4().hex}"

        conv_id = int(time.time_ns() % 10**12)
        db.execute(
            "INSERT INTO msg_conversations (id, conversation_type, owner_id,"
            " created_at, updated_at) VALUES (?, 'dm', ?, ?, ?)",
            (conv_id, other.id, now, now),
        )
        db.execute(
            "INSERT INTO msg_messages (id, conversation_id, author_id, content,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (conv_id + 1, conv_id, other.id, other_secret, now, now),
        )
        db.execute(
            "INSERT INTO auth_sessions (user_id, token_hash, ip_encrypted,"
            " ua_encrypted, created_at, expires_at, last_activity)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (other.id, other_token, "CIPHER", "CIPHER", now, now + 3600, now),
        )

        _req, request, artifact = _approve_and_generate(privacy_env)
        assert artifact["user_id"] == subject.id
        assert artifact["data"]["identity"]["id"] == subject.id
        payload = json.dumps(artifact, default=str)
        assert other_secret not in payload
        assert other_token not in payload
        assert other.username not in payload
        assert other.email not in payload
        assert request["status"] == "ready"


class TestThirdPartyPiiRegression:
    def test_friend_identity_redacted_in_generated_artifact(self, privacy_env):
        db = privacy_env["db"]
        subject = privacy_env["subject"]
        other = privacy_env["other"]
        now = int(time.time())
        db.execute(
            "INSERT INTO rel_friends (user_id, friend_id, created_at) VALUES (?, ?, ?)",
            (subject.id, other.id, now),
        )
        conv_id = int(time.time_ns() % 10**12)
        db.execute(
            "INSERT INTO msg_conversations (id, conversation_type, owner_id,"
            " created_at, updated_at) VALUES (?, 'dm', ?, ?, ?)",
            (conv_id, subject.id, now, now),
        )
        db.execute(
            "INSERT INTO msg_messages (id, conversation_id, author_id, content,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                conv_id + 1,
                conv_id,
                subject.id,
                f"met {other.username} today",
                now,
                now,
            ),
        )

        _req, _request, artifact = _approve_and_generate(privacy_env)
        friends = artifact["data"]["relationships"]["friends"]
        assert friends and friends[0]["friend_username"] == "[redacted]"
        payload = json.dumps(artifact, default=str)
        assert other.username not in payload
        assert subject.username in payload


class TestOversizedExportRegression:
    def test_oversized_export_fails_closed_without_artifact(
        self, privacy_env, monkeypatch
    ):
        from src.core.dsar.privacy import ExportQuotaExceeded
        import src.core.dsar.manager as manager_module

        db = privacy_env["db"]
        req = privacy_env["request_export"](privacy_env["subject"].id, format="json")
        db.execute(
            "UPDATE dsar_requests SET status = 'approved' WHERE id = ?", (req["id"],)
        )
        monkeypatch.setattr(
            manager_module,
            "export_quotas",
            lambda *a, **k: {
                "max_records_per_category": 100,
                "max_total_records": 200,
                "max_export_bytes": 1,
            },
        )
        with pytest.raises(ExportQuotaExceeded):
            manager_module.DSARManager(db).generate_manual(req["id"], admin_id=1)

        row = dict(
            db.fetch_one("SELECT * FROM dsar_requests WHERE id = ?", (req["id"],))
        )
        assert row["status"] == "failed"
        assert not row["storage_path"]
        assert not row["checksum"]
