"""
Regression tests for DSAR export scrubbing on cancel/expiry.

Live verification against an S3/RustFS-backed deployment showed a
``ready`` export surviving to the end of its retention window with the
object still in the bucket: ``_scrub_export_files`` hand-rolled a
``delete_object(Key=storage_path)`` call, but ``storage_path`` is the
backend-relative path. The object is written under the backend's
``path_prefix`` (``dsar-exports/…``), so the delete addressed a key that
never existed and the PII archive stayed put forever.

The scrub now deletes through the storage backend that wrote the export,
which applies the same prefix/base-directory it used on write.
"""

from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def dsar_env(db, auth_manager, pii_gen):
    from src.core.dsar import setup as dsar_setup
    from unittest.mock import patch
    from src.utils import encryption

    dsar_setup(db)
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username="dsarscrubuser",
            email=pii_gen.email(),
            password="TestPass123!",
        )
    return db, user


def _insert_request(db, user_id, *, backend, path, status="ready"):
    request_id = 9100000001
    now = int(time.time())
    db.execute(
        """INSERT INTO dsar_requests
           (id, user_id, status, requested_at, completed_at, expires_at,
            format, storage_backend, storage_path, checksum, file_size_bytes)
           VALUES (?, ?, ?, ?, ?, ?, 'json', ?, ?, 'deadbeef', 42)""",
        (request_id, user_id, status, now, now, now + 3600, backend, path),
    )
    return request_id


def _row(db, request_id):
    return dict(db.fetch_one("SELECT * FROM dsar_requests WHERE id = ?", (request_id,)))


class RecordingBackend:
    """Storage backend stub that records what the scrub asked it to delete."""

    def __init__(self, result=True, raises=None):
        self.deleted = []
        self._result = result
        self._raises = raises

    def delete(self, path):
        self.deleted.append(path)
        if self._raises is not None:
            raise self._raises
        return self._result


class TestScrubRemoteExport:
    def test_remote_export_is_deleted_through_the_storage_backend(
        self, dsar_env, monkeypatch
    ):
        """The backend gets the delete, so its own key layout is applied."""
        from src.core.dsar import export_formats as ef
        from src.core.dsar.manager import DSARManager

        db, user = dsar_env
        path = "dsar/2026/10/dsar_9100000001.json"
        request_id = _insert_request(db, user.id, backend="s3", path=path)

        backend = RecordingBackend()
        monkeypatch.setattr(
            ef.ExportFormatGenerator, "_build_storage", lambda self: backend
        )

        DSARManager(db)._scrub_export_files(_row(db, request_id))

        assert backend.deleted == [path]

    def test_legacy_absolute_path_is_removed_by_the_local_fallback(
        self, dsar_env, tmp_path
    ):
        """Rows written before paths were backend-relative still get cleaned."""
        from src.core.dsar.manager import DSARManager

        db, user = dsar_env
        legacy = tmp_path / "legacy-export.json"
        legacy.write_text('{"ok": true}')
        request_id = _insert_request(db, user.id, backend="local", path=str(legacy))

        DSARManager(db)._scrub_export_files(_row(db, request_id))

        assert not os.path.exists(legacy)

    def test_backend_delete_failure_is_logged_not_raised(self, dsar_env, monkeypatch):
        """A broken backend must not break cancel/expiry bookkeeping."""
        from src.core.dsar import export_formats as ef
        from src.core.dsar.manager import DSARManager

        db, user = dsar_env
        request_id = _insert_request(
            db, user.id, backend="s3", path="dsar/2026/10/dsar_9100000001.json"
        )

        backend = RecordingBackend(raises=OSError("backend unreachable"))
        monkeypatch.setattr(
            ef.ExportFormatGenerator, "_build_storage", lambda self: backend
        )

        DSARManager(db)._scrub_export_files(_row(db, request_id))

        assert backend.deleted  # the delete was attempted

    def test_unbuildable_backend_still_runs_the_local_fallback(
        self, dsar_env, tmp_path, monkeypatch
    ):
        """A construction failure (bad credentials, no client) must not skip cleanup."""
        from src.core.dsar import export_formats as ef
        from src.core.dsar.manager import DSARManager

        db, user = dsar_env
        legacy = tmp_path / "legacy-export.json"
        legacy.write_text('{"ok": true}')
        request_id = _insert_request(db, user.id, backend="s3", path=str(legacy))

        def _boom(self):
            raise RuntimeError("no S3 client")

        monkeypatch.setattr(ef.ExportFormatGenerator, "_build_storage", _boom)

        DSARManager(db)._scrub_export_files(_row(db, request_id))

        assert not os.path.exists(legacy)

    def test_s3_backend_delete_addresses_the_prefixed_key(self, monkeypatch):
        """Root cause: the object key carries the backend's path prefix.

        Any delete that bypasses the backend (a bare
        ``delete_object(Key=storage_path)``) targets a key that was never
        written, which is how the export survived in the bucket.
        """
        boto3 = pytest.importorskip("boto3")
        from src.core.dsar import export_formats as ef

        class FakeS3Client:
            def __init__(self):
                self.deleted = []

            def head_bucket(self, **_kwargs):
                return {}

            def delete_object(self, Bucket, Key):  # noqa: N803 - boto3 kwarg names
                self.deleted.append((Bucket, Key))

        client = FakeS3Client()
        monkeypatch.setattr(boto3, "client", lambda **_kwargs: client)

        settings = {
            "media": {
                "storage_backend": "s3",
                "s3_bucket": "plexichat-media",
                "s3_access_key": "key",
                "s3_secret_key": "secret",
                "s3_endpoint": "http://rustfs:9000",
            },
            "dsar": {},
        }
        monkeypatch.setattr(
            ef.config, "get", lambda key, default=None: settings.get(key, default)
        )

        path = "dsar/2026/10/dsar_9100000001.json"
        assert ef.ExportFormatGenerator().delete(path) is True
        assert client.deleted == [
            ("plexichat-media", f"dsar-exports/{path}"),
        ]

    def test_cancel_scrubs_the_export_file(self, dsar_env, monkeypatch):
        """The cancel path scrubs too, not just expiry."""
        from src.core.dsar import cancel_request
        from src.core.dsar import export_formats as ef

        db, user = dsar_env
        path = "dsar/2026/10/dsar_9100000001.json"
        # Only pending/approved requests can be cancelled, so give the row
        # an export path anyway - the scrub must still run.
        request_id = _insert_request(
            db, user.id, backend="s3", path=path, status="approved"
        )

        backend = RecordingBackend()
        monkeypatch.setattr(
            ef.ExportFormatGenerator, "_build_storage", lambda self: backend
        )

        cancel_request(request_id, user.id)

        assert backend.deleted == [path]
