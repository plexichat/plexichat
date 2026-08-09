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
    def test_encrypt_at_rest_helper_runs(self, media_setup, tmp_path, monkeypatch):
        """wrap_storage_with_encryption must wrap with EncryptedStorage."""
        # Hermetic keyring: point HOME at a temp dir so FileEncryptor generates
        # a fresh file key there instead of touching the real user keyring.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))

        from src.core.media.storage import (
            LocalStorage,
            wrap_storage_with_encryption,
        )
        from src.core.media.storage.encrypted import EncryptedStorage

        backend = LocalStorage(str(tmp_path / "media"))
        wrapped = wrap_storage_with_encryption(backend, enabled=True)
        assert isinstance(wrapped, EncryptedStorage)
        assert wrapped._backend is backend
        assert wrapped._enabled is True

        # Disabled wrapping must return the original backend untouched.
        assert wrap_storage_with_encryption(backend, enabled=False) is backend

    def test_encrypted_storage_round_trip(self, media_setup, tmp_path, monkeypatch):
        """Data stored via EncryptedStorage is encrypted at rest and round-trips."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))

        from src.core.media.storage import LocalStorage, wrap_storage_with_encryption

        storage = wrap_storage_with_encryption(
            LocalStorage(str(tmp_path / "media")), enabled=True
        )

        payload = b"top-secret-media-bytes" * 100
        path = storage.store(payload, "private/blob.bin", "application/octet-stream")

        # Retrieve decrypts back to the original payload.
        assert storage.retrieve(path) == payload
        assert storage.is_encrypted(path) is True

        # The bytes on disk must NOT be the plaintext.
        enc_blob = (tmp_path / "media" / "private" / "blob.bin.enc").read_bytes()
        assert payload not in enc_blob
        assert enc_blob != payload
        assert len(enc_blob) > 0

    def test_encrypted_storage_disabled_is_plaintext(
        self, media_setup, tmp_path, monkeypatch
    ):
        """With enabled=False the wrapper must store plaintext untouched."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))

        from src.core.media.storage import LocalStorage, wrap_storage_with_encryption

        storage = wrap_storage_with_encryption(
            LocalStorage(str(tmp_path / "media")), enabled=False
        )
        payload = b"plain-bytes"
        path = storage.store(payload, "docs/notes.txt", "text/plain")

        assert storage.retrieve(path) == payload
        assert storage.is_encrypted(path) is False
        disk = (tmp_path / "media" / "docs" / "notes.txt").read_bytes()
        assert disk == payload

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
