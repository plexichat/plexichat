"""Residual DSAR ciphertext leak: notification previews and poll questions.

The N680 fail-closed gate (``redact_internal_fields`` in
``DataCollector.collect_all``) denylists deterministic-ciphertext columns such
as ``ip_encrypted`` / ``old_content_encrypted`` but missed two columns that
``SELECT *`` collectors still project into the artifact:

* ``notif_notifications.content_preview_encrypted`` — notification message
  preview ciphertext, collected by ``NotificationsCollector``.
* ``poll_polls.question_encrypted`` — poll question ciphertext, collected by
  ``PollsCollector`` (whose plaintext ``question`` may deliberately be blank
  when at-rest encryption is enabled).

Both are non-vacuous end-to-end tests: they plant unique markers in the real
database, generate the artifact actually written to storage, and assert the
markers (and the internal field names) are absent from both JSON and ZIP
exports produced through the manual admin generation path. Both generation
paths (manual admin and harvester) share ``DataCollector.collect_all``, so the
same gate covers them.
"""

from __future__ import annotations

import io
import json
import time
import uuid
import zipfile
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def export_env(db, auth_manager, pii_gen, tmp_path, monkeypatch):
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
            username=f"cipher_{uuid.uuid4().hex[:10]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )
    return {
        "db": db,
        "subject": subject,
        "request_export": request_data_export,
    }


def _plant_rows(env):
    """Plant one notification and one poll owned by the data subject."""
    db = env["db"]
    uid = env["subject"].id
    now = int(time.time())
    preview_cipher = f"NOTIF_PREVIEW_CIPHER_{uuid.uuid4().hex}"
    question_cipher = f"POLL_QUESTION_CIPHER_{uuid.uuid4().hex}"
    preview_plain = f"preview-plaintext-{uuid.uuid4().hex[:8]}"

    notif_id = int(time.time_ns() % 10**10) * 10 + 1
    poll_id = notif_id + 1
    # FK parents for the notification (the DB enforces them).
    db.execute(
        """INSERT INTO msg_conversations
           (id, conversation_type, owner_id, created_at, updated_at)
           VALUES (?, 'dm', ?, ?, ?)""",
        (notif_id, uid, now, now),
    )
    db.execute(
        """INSERT INTO msg_messages
           (id, conversation_id, author_id, content, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (notif_id, notif_id, uid, "hello", now, now),
    )
    db.execute(
        """INSERT INTO notif_notifications
           (id, user_id, sender_id, message_id, conversation_id, mention_type,
            content_preview, content_preview_encrypted, created_at)
           VALUES (?, ?, ?, ?, ?, 'user', ?, ?, ?)""",
        (notif_id, uid, uid, notif_id, notif_id, preview_plain, preview_cipher, now),
    )
    db.execute(
        """INSERT INTO poll_polls
           (id, message_id, question, question_encrypted, created_by, created_at)
           VALUES (?, ?, '', ?, ?, ?)""",
        (poll_id, poll_id, question_cipher, uid, now),
    )
    return preview_cipher, question_cipher, preview_plain


def _generate(env, fmt):
    from src.core.dsar.export_formats import ExportFormatGenerator
    from src.core.dsar.manager import DSARManager

    db = env["db"]
    req = env["request_export"](env["subject"].id, format=fmt)
    db.execute(
        "UPDATE dsar_requests SET status = 'approved' WHERE id = ?", (req["id"],)
    )
    request = DSARManager(db).generate_manual(req["id"], admin_id=1)
    assert request["status"] == "ready"
    raw = ExportFormatGenerator(db=db).retrieve(request["storage_path"])
    if fmt == "zip":
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            return "\n".join(
                zf.read(name).decode("utf-8", "replace") for name in zf.namelist()
            )
    return raw.decode("utf-8", "replace")


class TestCiphertextFieldsNeverExported:
    @pytest.mark.parametrize("fmt", ["json", "zip"])
    def test_encrypted_preview_and_question_absent_from_artifact(self, export_env, fmt):
        preview_cipher, question_cipher, preview_plain = _plant_rows(export_env)

        artifact = _generate(export_env, fmt)

        # Non-vacuous: the planted notification row really reached the export.
        assert preview_plain in artifact
        # Fail closed: neither raw ciphertext nor the internal field names may
        # appear in an artifact handed to the data subject.
        assert preview_cipher not in artifact
        assert question_cipher not in artifact
        assert "content_preview_encrypted" not in artifact
        assert "question_encrypted" not in artifact

    def test_deeply_nested_ciphertext_fields_are_dropped(self):
        from src.core.dsar.privacy import redact_internal_fields

        payload = {
            "sessions": [
                {"rows": [{"level2": {"content_preview_encrypted": "CIPHER", "ok": 1}}]}
            ]
        }
        redacted = redact_internal_fields(payload)
        assert "content_preview_encrypted" not in json.dumps(redacted)
        assert redacted["sessions"][0]["rows"][0]["level2"]["ok"] == 1
        assert payload["sessions"][0]["rows"][0]["level2"]["content_preview_encrypted"] == "CIPHER"
