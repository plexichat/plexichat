"""Regression: a failed session INSERT must not orphan the pre-allocated file.

``create_session`` pre-allocates a sparse temp file (bounded to 10 GiB) BEFORE
inserting the session row. When the INSERT raises, the file used to be left on
disk with no session row, so ``cleanup_expired()`` -- which walks session rows
-- could never reap it. Every failed create therefore leaked an ownerless file.
"""

import os

import pytest

from src.core.media.chunked import ChunkedUploadManager


class _RaisingDB:
    """DB shim whose session INSERT fails; no active sessions exist."""

    def __init__(self):
        self.insert_called = False

    def fetch_one(self, sql, params=None):
        # active-session count query
        return {"cnt": 0}

    def fetch_all(self, *args, **kwargs):
        return []

    def execute(self, sql, params=None):
        if "INSERT INTO media_upload_sessions" in sql:
            self.insert_called = True
            raise RuntimeError("simulated DB write failure")
        return None


class _InsertCursor:
    """Minimal cursor: the atomic guarded INSERT reports one inserted row."""

    rowcount = 1


class _WorkingDB(_RaisingDB):
    def execute(self, sql, params=None):
        if "INSERT INTO media_upload_sessions" in sql:
            self.insert_called = True
            return _InsertCursor()
        return None


def _manager(tmp_path, db):
    mgr = ChunkedUploadManager(db)
    upload_tmp = tmp_path / "chunked_uploads"
    upload_tmp.mkdir()
    mgr._temp_dir = str(upload_tmp)
    return mgr, upload_tmp


def test_failed_insert_unlinks_preallocated_temp_file(tmp_path):
    db = _RaisingDB()
    mgr, upload_tmp = _manager(tmp_path, db)

    with pytest.raises(RuntimeError, match="simulated DB write failure"):
        mgr.create_session(
            user_id=1,
            filename="big.bin",
            content_type="application/octet-stream",
            total_size=1024 * 1024,
        )

    assert db.insert_called is True
    assert os.listdir(upload_tmp) == [], "orphaned temp file left after failed INSERT"


def test_successful_insert_keeps_preallocated_temp_file(tmp_path):
    """The cleanup must not fire on the happy path."""
    db = _WorkingDB()
    mgr, upload_tmp = _manager(tmp_path, db)

    session = mgr.create_session(
        user_id=1,
        filename="big.bin",
        content_type="application/octet-stream",
        total_size=1024 * 1024,
    )

    assert session is not None
    assert db.insert_called is True
    assert os.path.isfile(session.temp_path)
    assert len(os.listdir(upload_tmp)) == 1
