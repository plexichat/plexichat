"""DSAR export must not ship admin-only feature notes.

The N680 choke-point denylist (``redact_internal_fields``) strips secret
hashes, ciphertext, server storage paths, and (since N771) the plaintext
reviewer/admin notes ``internal_notes``, ``internal_notes_format`` and
``admin_notes``. It missed ``user_features.notes``: that column is documented
as "Admin notes" (``src/core/features/__init__.py``), is written only by the
admin feature-management path, and is collected verbatim by the ``SELECT *``
FeaturesCollector. It carries no secret suffix, so it survived the gate and
was shipped to the data subject.

These are non-vacuous end-to-end tests: plant unique markers in real rows,
generate the artifact actually written to storage (JSON and ZIP), prove the
subject's row reached the export, then assert the admin-only notes/field name
are gone. A unit test pins the suffix/denylist gate does not over-redact
legitimate subject data whose names merely contain secret-ish substrings.
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
            username=f"featnotes_{uuid.uuid4().hex[:10]}",
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

    # A legitimate subject-visible field and the admin-only notes, both unique
    # markers so we can prove the row reached the artifact and check redaction.
    badge_plain = f"badge-vip-{uuid.uuid4().hex[:8]}"
    admin_notes_marker = f"ADMIN_FEATURE_NOTE_{uuid.uuid4().hex}"
    fid = int(time.time_ns() % 10**10) * 10 + 3
    db.execute(
        """INSERT INTO user_features
           (id, user_id, rate_limit_tier, badges, granted_by, granted_at, notes)
           VALUES (?, ?, 'premium', ?, ?, ?, ?)""",
        (fid, uid, json.dumps([badge_plain]), 1, now, admin_notes_marker),
    )
    return badge_plain, admin_notes_marker


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


class TestAdminFeatureNotesNeverExported:
    @pytest.mark.parametrize("fmt", ["json", "zip"])
    def test_admin_feature_notes_absent_from_artifact(self, export_env, fmt):
        badge_plain, admin_notes_marker = _plant_rows(export_env)

        artifact = _generate(export_env, fmt)

        # Non-vacuous: the subject's own feature row really reached the export.
        assert badge_plain in artifact
        # Fail closed: admin-only notes must never reach the subject.
        assert admin_notes_marker not in artifact
        assert '"notes"' not in artifact


def test_denylist_does_not_over_redact_legitimate_data():
    """The gate must drop admin-only notes, not ordinary subject fields.

    ``key_result``/``token_index``/``checksum``/``reason``/``metadata`` look
    secret-ish by substring but are legitimate subject data; the endswith /
    denylist rule preserves them. This pins that behaviour so adding ``notes``
    to the denylist cannot silently strip legitimate export data.
    """
    from src.core.dsar.privacy import redact_internal_fields

    payload = {
        "features": [
            {
                "key_result": "alice",
                "token_index": 3,
                "checksum": "sha256:deadbeef",
                "reason": "vip",
                "metadata": "subject-meta",
                "notes": "ADMIN-ONLY",
                "client_secret_hash": "SECRET",
                "ip_encrypted": "CIPHER",
            }
        ]
    }
    redacted = redact_internal_fields(payload)
    row = redacted["features"][0]
    assert row["key_result"] == "alice"
    assert row["token_index"] == 3
    assert row["checksum"] == "sha256:deadbeef"
    assert row["reason"] == "vip"
    assert row["metadata"] == "subject-meta"
    assert "notes" not in row
    assert "client_secret_hash" not in row
    assert "ip_encrypted" not in row
    # Input is not mutated.
    assert payload["features"][0]["notes"] == "ADMIN-ONLY"
