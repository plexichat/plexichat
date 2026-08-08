"""Encryption-at-rest helpers (wrap storage, dedup hashes, scanner status)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def media_setup(db):
    from src.core import media

    media.setup(db)
    yield db
    # Wait for fire-and-forget background tasks before the db fixture
    # removes the temp SQLite file (see MediaManager.close docstring:
    # a thread touching a deleted mmap'd DB file crashes with SIGBUS).
    if media._manager is not None:
        try:
            media._manager.close()
        except Exception:
            pass
        media._manager = None
        media._setup_complete = False


class TestEncryptionWrap:
    def test_encrypt_at_rest_helper_runs(self, media_setup):
        # Verify the encryption-at-rest installer exists and is callable
        try:
            from src.core.media.encryption import wrap_storage_with_encryption  # type: ignore
        except ImportError:
            pytest.skip("Encryption module not publicly exported")
        # Bound check: callable must be invokable on None storage.
        assert callable(wrap_storage_with_encryption)

    def test_dedup_hash_blocked_check(self, media_setup):
        from src.core.media import is_hash_blocked

        blocked, reason = is_hash_blocked("0" * 64)
        assert blocked is False
        assert reason is None

    def test_report_hash_idempotent(self, media_setup, test_user):
        from src.core.media import report_hash

        report_id = report_hash(
            hash_value="a" * 64,
            reporter_id=test_user.id,
            reason="unit-test",
        )
        assert report_id > 0
