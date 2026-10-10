"""
Regression tests for DSAR bugs fixed during the audit.

D1: get_export_file flipped the status to 'downloaded' *before* the bytes
    were streamed, so a failed/partial download became an unrecoverable 400.
    The status must only advance after a successful stream.
D3: request_export did not validate the ``format`` argument, allowing
    arbitrary (unsupported) formats to be stored.
"""

import time

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def dsar_user(db, auth_manager, pii_gen):
    from src.core.dsar import setup as dsar_setup
    from unittest.mock import patch
    from src.utils import encryption

    dsar_setup(db)
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username="dsarreguser",
            email=pii_gen.email(),
            password="TestPass123!",
        )
    return db, user


class TestExportFileStatusFlip:
    """D1: status must not flip to 'downloaded' on read."""

    def _make_ready(self, db, user_id, path):
        req_id = 9000000001
        now = int(time.time())
        db.execute(
            """INSERT INTO dsar_requests
               (id, user_id, status, requested_at, completed_at, expires_at,
                format, storage_backend, storage_path, checksum, file_size_bytes)
               VALUES (?, ?, 'ready', ?, ?, ?, 'json', 'local', ?, 'deadbeef', 42)""",
            (req_id, user_id, now, now, now + 3600, path),
        )
        return req_id

    def test_get_export_file_does_not_flip_status(self, dsar_user, tmp_path):
        from src.core.dsar import get_export_file

        db, user = dsar_user
        f = tmp_path / "export.json"
        f.write_text('{"ok": true}')
        req_id = self._make_ready(db, user.id, str(f))

        info = get_export_file(req_id, user.id)
        assert info is not None
        assert info["status"] == "ready"

        row = db.fetch_one("SELECT status FROM dsar_requests WHERE id = ?", (req_id,))
        assert row["status"] == "ready"

    def test_mark_export_downloaded_advances_status(self, dsar_user, tmp_path):
        from src.core.dsar import get_export_file, mark_export_downloaded

        db, user = dsar_user
        f = tmp_path / "export.json"
        f.write_text('{"ok": true}')
        req_id = self._make_ready(db, user.id, str(f))

        get_export_file(req_id, user.id)
        mark_export_downloaded(req_id, user.id)

        row = db.fetch_one("SELECT status FROM dsar_requests WHERE id = ?", (req_id,))
        assert row["status"] == "downloaded"

    def test_downloaded_still_re_downloadable_until_cleaned(self, dsar_user, tmp_path):
        from src.core.dsar import get_export_file, mark_export_downloaded

        db, user = dsar_user
        f = tmp_path / "export.json"
        f.write_text('{"ok": true}')
        req_id = self._make_ready(db, user.id, str(f))

        get_export_file(req_id, user.id)
        mark_export_downloaded(req_id, user.id)

        # After a successful download the row is 'downloaded' but the file is
        # still present, so the client can re-download (no permanent 400).
        info = get_export_file(req_id, user.id)
        assert info is not None
        assert info["status"] == "downloaded"


class TestRequestExportFormatValidation:
    """D3: unsupported formats must be rejected."""

    def test_rejects_unsupported_format(self, dsar_user):
        from src.core.dsar import request_data_export

        db, user = dsar_user
        with pytest.raises(ValueError):
            request_data_export(user.id, format="pdf")

    def test_accepts_json(self, dsar_user):
        from src.core.dsar import request_data_export

        db, user = dsar_user
        result = request_data_export(user.id, format="json")
        assert result is not None
        assert result["format"] == "json"

    def test_accepts_zip(self, dsar_user):
        from src.core.dsar import request_data_export

        db, user = dsar_user
        result = request_data_export(user.id, format="zip")
        assert result is not None
        assert result["format"] == "zip"
