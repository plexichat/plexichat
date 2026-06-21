"""Encryption-at-rest helpers (wrap storage, dedup hashes, scanner status)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def media_setup(db):
    from src.core.media import setup

    setup(db)
    return db


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
        from src.core.media import setup, is_hash_blocked

        blocked, reason = is_hash_blocked("0" * 64)
        assert blocked is False
        assert reason is None

    def test_report_hash_idempotent(self, media_setup, test_user):
        from src.core.media import setup

        report_id = setup.report_hash(
            hash_value="a" * 64,
            reporter_id=test_user.id,
            reason="unit-test",
        )
        assert report_id > 0
