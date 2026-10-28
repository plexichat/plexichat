"""Regression: account erasure must not leave DSAR rows or export objects.

``dsar_requests`` has no foreign key to ``auth_users``, so the reaper's
purge never touched it. After a force purge the rows survived holding the
erased user's id and, for a ``ready`` request, the storage path of an
export archive that is full PII. ``purge_user`` now scrubs the export
through the storage backend that wrote it and deletes the manifest and
request rows, recording any path it could not delete on the PURGED event.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from unittest.mock import patch

import pytest

from src.core.auth.deletion_log import DeletionLog
from src.utils import encryption
from src.core.auth.reaper import AccountReaper

pytestmark = pytest.mark.integration


def _register(auth_manager, pii_gen, label="dsarreap"):
    unique = uuid.uuid4().hex[:8]
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        return auth_manager.register(
            username=f"{label}_{unique}",
            email=pii_gen.email(),
            password="TestPass123!",
        )


@pytest.fixture
def dsar_env(db, auth_manager, pii_gen, tmp_path):
    from src.core.dsar import setup as dsar_setup

    dsar_setup(db)
    user = _register(auth_manager, pii_gen)
    reaper = AccountReaper(db, auth_manager)
    log_path = tmp_path / "deletion_log.jsonl"
    reaper._log = DeletionLog(file_path=str(log_path))
    return db, user, reaper, log_path


def _insert_request(db, user_id, *, path, backend="s3", status="ready"):
    from src.utils.encryption import generate_snowflake_id

    request_id = generate_snowflake_id()
    now = int(time.time())
    db.execute(
        """INSERT INTO dsar_requests
           (id, user_id, status, requested_at, completed_at, expires_at,
            format, storage_backend, storage_path, checksum, file_size_bytes)
           VALUES (?, ?, ?, ?, ?, ?, 'json', ?, ?, 'deadbeef', 42)""",
        (request_id, user_id, status, now, now, now + 3600, backend, path),
    )
    db.execute(
        """INSERT INTO dsar_export_manifest
           (id, request_id, table_name, record_count, exported_at)
           VALUES (?, ?, 'msg_messages', 3, ?)""",
        (generate_snowflake_id(), request_id, now),
    )
    return request_id


def _purged_metadata(log_path):
    entries = [
        json.loads(line) for line in log_path.read_text().splitlines() if line.strip()
    ]
    purged = [e for e in entries if e.get("action") == "PURGED"]
    assert purged, "expected a PURGED event in the deletion log"
    return purged[-1].get("metadata") or {}


class RecordingBackend:
    """Storage backend stub that records deletes and reports a result."""

    def __init__(self, result=True):
        self.deleted = []
        self._result = result

    def delete(self, path):
        self.deleted.append(path)
        return self._result


class TestReaperDsarPurge:
    def test_purge_deletes_dsar_rows_and_manifest(self, dsar_env):
        db, user, reaper, _ = dsar_env
        request_id = _insert_request(db, user.id, path="dsar/2026/10/dsar_export.json")

        reaper.purge_user(user.id, user.username)

        assert (
            db.fetch_one("SELECT id FROM dsar_requests WHERE id = ?", (request_id,))
            is None
        )
        assert (
            db.fetch_one(
                "SELECT id FROM dsar_export_manifest WHERE request_id = ?",
                (request_id,),
            )
            is None
        )
        assert (
            db.fetch_one("SELECT id FROM auth_users WHERE id = ?", (user.id,)) is None
        )

    def test_purge_scrubs_the_export_object(self, dsar_env, monkeypatch):
        from src.core.dsar import export_formats as ef

        db, user, reaper, _ = dsar_env
        path = "dsar/2026/10/dsar_export.json"
        _insert_request(db, user.id, path=path)

        backend = RecordingBackend()
        monkeypatch.setattr(
            ef.ExportFormatGenerator, "_build_storage", lambda self: backend
        )

        reaper.purge_user(user.id, user.username)

        assert backend.deleted == [path]

    def test_unswept_export_is_recorded_for_an_operator(self, dsar_env, monkeypatch):
        from src.core.dsar import export_formats as ef

        db, user, reaper, log_path = dsar_env
        path = "dsar/2026/10/dsar_export.json"
        _insert_request(db, user.id, path=path)

        backend = RecordingBackend(result=False)
        monkeypatch.setattr(
            ef.ExportFormatGenerator, "_build_storage", lambda self: backend
        )

        reaper.purge_user(user.id, user.username)

        # The erasure still completes; the leftover is reported, not raised.
        assert (
            db.fetch_one("SELECT id FROM auth_users WHERE id = ?", (user.id,)) is None
        )
        metadata = _purged_metadata(log_path)
        assert metadata["dsar_exports_unswept"] == 1
        assert metadata["dsar_export_paths"] == [path]

    def test_legacy_absolute_export_path_is_removed(
        self, dsar_env, tmp_path, monkeypatch
    ):
        from src.core.dsar import export_formats as ef

        db, user, reaper, _ = dsar_env
        legacy = tmp_path / "legacy-dsar-export.json"
        legacy.write_text('{"subject": "erased"}')
        _insert_request(db, user.id, path=str(legacy), backend="local")

        # A backend that cannot match the legacy absolute path.
        backend = RecordingBackend(result=False)
        monkeypatch.setattr(
            ef.ExportFormatGenerator, "_build_storage", lambda self: backend
        )

        reaper.purge_user(user.id, user.username)

        assert not os.path.exists(legacy)

    def test_other_users_requests_are_untouched(
        self, dsar_env, auth_manager, pii_gen, monkeypatch
    ):
        from src.core.dsar import export_formats as ef

        db, user, reaper, _ = dsar_env
        other = _register(auth_manager, pii_gen, label="dsarkeep")
        path = "dsar/2026/10/dsar_keep.json"
        keep_id = _insert_request(db, other.id, path=path)

        backend = RecordingBackend()
        monkeypatch.setattr(
            ef.ExportFormatGenerator, "_build_storage", lambda self: backend
        )

        reaper.purge_user(user.id, user.username)

        row = db.fetch_one("SELECT user_id FROM dsar_requests WHERE id = ?", (keep_id,))
        assert row is not None and row["user_id"] == other.id
        assert backend.deleted == []

    def test_purge_without_dsar_requests_is_a_no_op(self, dsar_env):
        db, user, reaper, log_path = dsar_env

        reaper.purge_user(user.id, user.username)

        assert (
            db.fetch_one("SELECT id FROM auth_users WHERE id = ?", (user.id,)) is None
        )
        assert "dsar_exports_unswept" not in _purged_metadata(log_path)
