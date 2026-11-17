"""Regression tests for backend security hardening in the encryption tooling.

Covers:
  * KEK migration CLI keyring-name path traversal (arbitrary file overwrite).
  * KEK migration metadata written with safe 0600 permissions.
  * Streaming file-encryption length/version metadata tampering (silent
    plaintext truncation) via versioned AAD binding.
"""

import io
import json
import os
import struct
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag

from src.utils.encryption.file_encryption import (
    STREAM_MAGIC,
    STREAM_VERSION,
    Keyring,
    StreamingFileEncryptor,
)
from src.utils.encryption.kek_migration import cli as kek_cli
from src.utils.encryption.kek_migration.migration import KeyringMigration

OLD_KEK_HEX = "ab" * 32
NEW_KEK_HEX = "cd" * 32


class TestKeyringNamePathTraversal:
    @pytest.fixture
    def home(self, tmp_path, monkeypatch):
        data_dir = tmp_path / ".plexichat" / "data"
        data_dir.mkdir(parents=True)
        monkeypatch.setattr(
            "pathlib.Path.home", classmethod(lambda cls: tmp_path)
        )
        return tmp_path, data_dir

    @pytest.mark.parametrize(
        "bad_name",
        [
            "../../etc/passwd",
            "../system_keyring.json",
            "/etc/passwd",
            "subdir/keyring.json",
            "subdir\\keyring.json",
            "..",
            ".",
            "",
        ],
    )
    def test_resolve_rejects_unsafe_names(self, home, bad_name):
        with pytest.raises(ValueError):
            kek_cli._resolve_keyring_path(bad_name)

    def test_resolve_accepts_plain_filename(self, home):
        _, data_dir = home
        resolved = kek_cli._resolve_keyring_path("message_keyring.json")
        assert resolved == (data_dir / "message_keyring.json").resolve()

    def test_resolve_rejects_symlink_leaving_data_dir(self, home):
        _, data_dir = home
        outside = home[0] / "outside.json"
        outside.write_text("secret")
        link = data_dir / "linked.json"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable")
        with pytest.raises(ValueError):
            kek_cli._resolve_keyring_path("linked.json")

    def test_rollback_ignores_traversal_name(self, home):
        tmp_path, _ = home
        outside = tmp_path / "victim.json"
        outside.write_text("original")
        assert kek_cli.rollback_keyring("../victim.json") is False
        assert outside.read_text() == "original"

    def test_migrate_ignores_traversal_name(self, home, monkeypatch):
        tmp_path, _ = home
        outside = tmp_path / "victim.json"
        outside.write_text("original")
        monkeypatch.setenv("PLEXICHAT_TEST_OLD", OLD_KEK_HEX)
        monkeypatch.setenv("PLEXICHAT_TEST_NEW", NEW_KEK_HEX)
        assert (
            kek_cli.migrate_keyring(
                "../victim.json", "PLEXICHAT_TEST_OLD", "PLEXICHAT_TEST_NEW"
            )
            is False
        )
        assert outside.read_text() == "original"


class TestMigrationMetadataPermissions:
    def test_metadata_file_is_owner_only(self, tmp_path):
        keyring_path = tmp_path / "system_keyring.json"
        migrator = KeyringMigration(keyring_path)
        migrator._save_migration_metadata(
            {"current_version": 1, "keys": {"1": {}}, "rotated_at": 0},
            bytes.fromhex(OLD_KEK_HEX),
            bytes.fromhex(NEW_KEK_HEX),
        )
        meta = migrator.metadata_path
        assert meta.exists()
        data = json.loads(meta.read_text())
        assert "old_kek_hash" in data and "new_kek_hash" in data
        if os.name == "posix":
            assert meta.stat().st_mode & 0o077 == 0


class _StreamHarness:
    def __init__(self, tmp_path, chunk_size=64):
        self.keyring = Keyring(tmp_path / "keyring.json")
        self.encryptor = StreamingFileEncryptor(self.keyring, chunk_size=chunk_size)

    @staticmethod
    def _header_size_offset(blob: bytes) -> int:
        assert blob[:5] == STREAM_MAGIC
        wrapped_key_len = struct.unpack(">H", blob[10:12])[0]
        return 5 + 1 + 4 + 2 + wrapped_key_len

    def decrypt(self, blob: bytes) -> bytes:
        out = io.BytesIO()
        self.encryptor.decrypt_stream(io.BytesIO(blob), out)
        return out.getvalue()


class TestStreamMetadataIntegrity:
    @pytest.fixture
    def harness(self, tmp_path):
        return _StreamHarness(tmp_path)

    def test_happy_path_uses_v3(self, harness):
        data = os.urandom(64 * 5)
        out = io.BytesIO()
        harness.encryptor.encrypt_stream(io.BytesIO(data), out, len(data))
        blob = out.getvalue()
        assert blob[5] == STREAM_VERSION
        assert harness.decrypt(blob) == data

    def test_tampered_file_size_is_rejected(self, harness):
        data = os.urandom(64 * 5)
        out = io.BytesIO()
        harness.encryptor.encrypt_stream(io.BytesIO(data), out, len(data))
        blob = bytearray(out.getvalue())

        offset = harness._header_size_offset(blob)
        original_size = struct.unpack(">Q", blob[offset : offset + 8])[0]
        # Shrink the declared plaintext size. Against v2 every surviving
        # chunk still authenticated and the shorter plaintext was returned
        # with verified=True; v3 binds the size into each chunk's AAD, so
        # the very first chunk now fails its GCM tag check.
        struct.pack_into(">Q", blob, offset, original_size // 2)

        with pytest.raises((InvalidTag, Exception)):
            harness.decrypt(bytes(blob))

    def test_version_downgrade_is_rejected(self, harness):
        data = os.urandom(64 * 3)
        out = io.BytesIO()
        harness.encryptor.encrypt_stream(io.BytesIO(data), out, len(data))
        blob = bytearray(out.getvalue())
        blob[5] = 2  # downgrade to legacy AAD scheme
        with pytest.raises((InvalidTag, Exception)):
            harness.decrypt(bytes(blob))
