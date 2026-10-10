"""
Regression tests for media audit fixes.

Covers:
- MD-8: the URL signer must never fall back to a publicly-known default key;
  an unset / insecure ``signing_key`` is replaced with an ephemeral random key.
- MD-9: ``_read_stream_to_temp`` must delete its temp file if the copy fails.
"""

import os
import sqlite3
import tempfile

import pytest


class TestSigningKey:
    def test_insecure_default_signing_key_is_replaced(self, media_module):
        manager = media_module._manager
        manager._config["signing_key"] = "change-this-secret-key"

        signer = manager._init_url_signer()

        # The hardcoded default must no longer be used.
        assert manager._config["signing_key"] != "change-this-secret-key"
        # And the generated signer must actually sign + verify.
        signed = signer.sign_url("/api/v1/media/attachments/x.png", 1)
        assert signer.verify_url(signed.url)[0] is True

    def test_missing_signing_key_generates_ephemeral_key(self, media_module):
        manager = media_module._manager
        manager._config.pop("signing_key", None)

        signer = manager._init_url_signer()

        assert manager._config.get("signing_key")
        signed = signer.sign_url("/api/v1/media/attachments/y.png", 2)
        assert signer.verify_url(signed.url)[0] is True


class TestUploadTempFileCleanup:
    def test_read_stream_to_temp_cleans_up_on_error(self, media_module, monkeypatch):
        recorded = {}
        original = tempfile.NamedTemporaryFile

        def tracking_named_temp_file(*args, **kwargs):
            f = original(*args, **kwargs)
            recorded["name"] = f.name
            return f

        monkeypatch.setattr(tempfile, "NamedTemporaryFile", tracking_named_temp_file)

        class BadStream:
            def read(self, n=-1):
                raise IOError("simulated read failure")

        manager = media_module._manager
        with pytest.raises(Exception):
            manager._read_stream_to_temp(BadStream())

        # A temp file was created but must have been removed on the error path.
        assert recorded, "expected a temp file to have been created"
        assert not os.path.exists(recorded["name"])


class TestPermanentDeleteRetry:
    """MD-10: permanent delete is resilient to transient WAL write-lock contention.

    ``upload_file`` spawns fire-and-forget tasks (scan, thumbnails, pHash, dedup)
    that write to the database after the upload returns. A synchronous
    ``delete_file_permanently`` of that same file can race with them. WAL mode
    allows a single writer at a time, so the delete's write can transiently
    fail with ``database is locked``; the whole transaction is retried (the
    ``execute`` layer deliberately never retries a statement inside an open
    transaction).
    """

    def test_is_db_lock_error_matches_sqlite_busy(self):
        from src.core.media._files import _is_db_lock_error

        assert _is_db_lock_error(sqlite3.OperationalError("database is locked"))
        assert _is_db_lock_error(sqlite3.OperationalError("database table is locked"))
        assert _is_db_lock_error(sqlite3.OperationalError("database is busy"))
        assert not _is_db_lock_error(sqlite3.OperationalError("no such table: x"))
        assert not _is_db_lock_error(RuntimeError("boom"))

    def test_retry_on_db_lock_retries_transient_then_succeeds(
        self, media_module, monkeypatch
    ):
        manager = media_module._manager
        calls = {"n": 0}
        sleeps = []

        monkeypatch.setattr("src.core.media._files.time.sleep", sleeps.append)

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise sqlite3.OperationalError("database is locked")

        manager._retry_on_db_lock(flaky)

        assert calls["n"] == 3
        assert len(sleeps) == 2

    def test_retry_on_db_lock_does_not_retry_other_errors(
        self, media_module, monkeypatch
    ):
        manager = media_module._manager
        calls = {"n": 0}
        monkeypatch.setattr("src.core.media._files.time.sleep", lambda _s: None)

        def boom():
            calls["n"] += 1
            raise ValueError("not a lock error")

        with pytest.raises(ValueError):
            manager._retry_on_db_lock(boom)
        assert calls["n"] == 1

    def test_retry_on_db_lock_gives_up_after_attempts(self, media_module, monkeypatch):
        from src.core.media._files import _LOCK_RETRY_ATTEMPTS

        manager = media_module._manager
        calls = {"n": 0}
        monkeypatch.setattr("src.core.media._files.time.sleep", lambda _s: None)

        def always_locked():
            calls["n"] += 1
            raise sqlite3.OperationalError("database is locked")

        with pytest.raises(sqlite3.OperationalError):
            manager._retry_on_db_lock(always_locked)
        assert calls["n"] == _LOCK_RETRY_ATTEMPTS
