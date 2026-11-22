"""DSAR export must not ship internal-only admin notes.

The N680 choke-point denylist (``redact_internal_fields``) strips secret
hashes, ciphertext and server storage paths from every collected category. It
also strips ``feedback.internal_notes_encrypted`` (via the ``_encrypted``
suffix) — but the *plaintext* twin ``feedback.internal_notes`` and the report
tables' admin-only ``admin_notes`` carry no redaction suffix, so they survive
the gate and are written verbatim into the artifact handed to the data
subject. Those columns are documented as admin/reviewer-only, have no place in
an Article 15/20 portability export, and are the exact class of internal-only
column the choke point exists to remove.

These are non-vacuous end-to-end tests: they plant unique markers in real
rows, generate the artifact actually written to storage (JSON and ZIP), prove
the rows reached the export, then assert the internal markers/field names are
gone. A unit test also pins that the suffix rule does not over-redact
legitimate subject data whose name merely contains a "key"/"nonce" token.
"""

from __future__ import annotations

import io
import time
import uuid
import zipfile
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def export_env(db, auth_manager, pii_gen, tmp_path, monkeypatch):
    from src.core.dsar import request_data_export
    from src.core.dsar import setup as dsar_setup
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
            username=f"notes_{uuid.uuid4().hex[:10]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )
    return {
        "db": db,
        "subject": subject,
        "request_export": request_data_export,
    }


def _plant_rows(env):
    db = env["db"]
    uid = env["subject"].id
    now = int(time.time())

    feedback_plain = f"feedback-body-{uuid.uuid4().hex[:8]}"
    feedback_internal = f"INTERNAL_FEEDBACK_NOTE_{uuid.uuid4().hex}"
    report_plain = f"report-reason-{uuid.uuid4().hex[:8]}"
    report_internal = f"INTERNAL_REPORT_ADMIN_NOTE_{uuid.uuid4().hex}"

    fb_id = int(time.time_ns() % 10**10) * 10 + 1
    rep_id = fb_id + 1
    db.execute(
        """INSERT INTO feedback
           (id, user_id, content, category, rating, created_at, status,
            internal_notes, internal_notes_format)
           VALUES (?, ?, ?, 'bug', 3, ?, 'open', ?, 'plain')""",
        (fb_id, uid, feedback_plain, now, feedback_internal),
    )
    db.execute(
        """INSERT INTO message_reports
           (id, message_id, channel_id, reporter_id, reported_user_id, reason,
            category, reported_at, admin_notes)
           VALUES (?, ?, ?, ?, ?, ?, 'spam', ?, ?)""",
        (rep_id, fb_id, fb_id, uid, uid, report_plain, now, report_internal),
    )
    return feedback_plain, feedback_internal, report_plain, report_internal


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


class TestInternalNotesNeverExported:
    @pytest.mark.parametrize("fmt", ["json", "zip"])
    def test_internal_admin_notes_absent_from_artifact(self, export_env, fmt):
        feedback_plain, feedback_internal, report_plain, report_internal = _plant_rows(
            export_env
        )

        artifact = _generate(export_env, fmt)

        # Non-vacuous: the subject's own rows really reached the export.
        assert feedback_plain in artifact
        assert report_plain in artifact
        # Fail closed: admin/reviewer-only notes must never reach the subject.
        assert feedback_internal not in artifact
        assert report_internal not in artifact
        assert "internal_notes" not in artifact
        assert "admin_notes" not in artifact


def test_suffix_rule_does_not_over_redact_legitimate_data():
    """The suffix gate must drop only internal material, not ordinary fields.

    ``key_result``/``token_index``/``checksum`` look secret-ish by substring
    but do not carry an internal suffix and belong to the subject; the current
    endswith-based rule preserves them. This pins that behaviour so a future
    widening of the rule does not silently strip legitimate export data.
    """
    from src.core.dsar.privacy import redact_internal_fields

    payload = {
        "results": [
            {
                "key_result": "alice",
                "token_index": 3,
                "checksum": "sha256:deadbeef",
                "nonce": "plain-field",
                "client_secret_hash": "SECRET",
                "ip_encrypted": "CIPHER",
            }
        ]
    }
    redacted = redact_internal_fields(payload)
    row = redacted["results"][0]
    assert row["key_result"] == "alice"
    assert row["token_index"] == 3
    assert row["checksum"] == "sha256:deadbeef"
    assert row["nonce"] == "plain-field"
    assert "client_secret_hash" not in row
    assert "ip_encrypted" not in row
    # Input is not mutated.
    assert payload["results"][0]["client_secret_hash"] == "SECRET"
