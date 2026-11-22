"""DSAR artifacts must not embed secret hashes or internal storage paths.

Several collectors project ``SELECT *`` straight into the export payload. Two
classes of internal-only fields then reach the artifact that is handed to the
data subject:

* ``app_applications.client_secret_hash`` — ``ApplicationsCollector`` tries to
  redact ``bot_token_encrypted`` / ``public_key_encrypted``, but those columns
  do not exist in the live schema, so the real secret hash is exported.
* ``sum_workbooks.blob_path`` / ``sum_snapshots.blob_path`` — server-side
  ``.plexisum`` object paths. The admin data-export API deliberately strips
  ``storage_path`` (server filesystem/object layout), and the media collector
  documents that internal storage paths are excluded from DSAR exports; the
  suite collector leaks the equivalent field.

This is a non-vacuous end-to-end test: it generates a real export, reads the
artifact actually written to storage, and asserts the planted values are absent.
"""

from __future__ import annotations

import json
import time
import uuid
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
            username=f"redact_{uuid.uuid4().hex[:10]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )
    return {
        "db": db,
        "subject": subject,
        "request_export": request_data_export,
    }


def _generate_artifact(env):
    from src.core.dsar.export_formats import ExportFormatGenerator
    from src.core.dsar.manager import DSARManager

    db = env["db"]
    req = env["request_export"](env["subject"].id, format="json")
    db.execute(
        "UPDATE dsar_requests SET status = 'approved' WHERE id = ?", (req["id"],)
    )
    request = DSARManager(db).generate_manual(req["id"], admin_id=1)
    assert request["status"] == "ready"
    return json.loads(ExportFormatGenerator(db=db).retrieve(request["storage_path"]))


class TestInternalFieldsNeverExported:
    def test_secret_hashes_and_blob_paths_are_not_in_generated_artifact(
        self, export_env
    ):
        db = export_env["db"]
        uid = export_env["subject"].id
        now = int(time.time())
        secret_hash = f"CLIENT_SECRET_HASH_{uuid.uuid4().hex}"
        blob_path = f"/srv/plexichat/data/suite/{uuid.uuid4().hex}.plexisum"
        snap_path = f"/srv/plexichat/data/suite/{uuid.uuid4().hex}.snap"

        base_id = int(time.time_ns() % 10**10) * 10
        app_id, book_id, snap_id = base_id + 1, base_id + 2, base_id + 3
        db.execute(
            """INSERT INTO app_applications
               (id, owner_id, name, client_secret_hash, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (app_id, uid, "myapp", secret_hash, now, now),
        )
        db.execute(
            """INSERT INTO sum_workbooks
               (id, owner_id, title, blob_path, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (book_id, uid, "book", blob_path, now, now),
        )
        db.execute(
            """INSERT INTO sum_snapshots
               (id, workbook_id, created_by, revision, blob_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (snap_id, book_id, uid, 1, snap_path, now),
        )

        artifact = _generate_artifact(export_env)
        payload = json.dumps(artifact, default=str)

        # The planted internal values must never reach the data subject.
        assert secret_hash not in payload
        assert blob_path not in payload
        assert snap_path not in payload

        # The fields themselves must be gone (or replaced with a redaction
        # marker), not merely renamed; a hash/path key with any value is still
        # internal metadata that has no place in a portability export.
        assert "client_secret_hash" not in payload
        assert "blob_path" not in payload
