"""
Tests for explicit, idempotent forward migration when a keyring was decrypted
via the PLEXICHAT_SYSTEM_KEY fallback path.

Runtime Keyring behavior (distinct from the kek_migration CLI tool): a fallback
read keeps recovery possible, but when the dedicated KEK is available the
keyring is rewrapped under it on load (guaranteed, not deferred to an
incidental save). Writes refuse to persist while migration is unsafe.
"""

import base64
import json
import os

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.utils.encryption.core.keyring import (
    Keyring,
    KeyringMigrationError,
)


def _encode_env_key(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _write_keyring(path, kek: bytes, data: dict) -> None:
    aesgcm = AESGCM(kek)
    nonce = os.urandom(12)
    payload = aesgcm.encrypt(nonce, json.dumps(data).encode(), None)
    path.write_text(
        json.dumps(
            {
                "nonce": base64.b64encode(nonce).decode(),
                "payload": base64.b64encode(payload).decode(),
            }
        )
    )


def _decrypt_keyring(path, kek: bytes) -> dict:
    encrypted = json.loads(path.read_text())
    aesgcm = AESGCM(kek)
    nonce = base64.b64decode(encrypted["nonce"])
    payload = base64.b64decode(encrypted["payload"])
    return json.loads(aesgcm.decrypt(nonce, payload, None))


def _sample_data():
    return {
        "current_version": 2,
        "current_key_source": "generated",
        "rotated_at": 1700000000,
        "keys": {
            "1": base64.b64encode(b"\x11" * 32).decode(),
            "2": base64.b64encode(b"\x22" * 32).decode(),
        },
        "blind_index_root_key": base64.b64encode(b"\x33" * 32).decode(),
        "pepper_keys": {"v1": base64.b64encode(b"\x44" * 32).decode()},
    }


@pytest.fixture(autouse=True)
def _clear_vault_machine_key_cache():
    """Prevent cross-test contamination from the module-level machine-key cache."""
    try:
        from src.utils.encryption import vault

        vault._load_machine_key_cached.cache_clear()
    except (AttributeError, ImportError):
        pass
    yield
    try:
        from src.utils.encryption import vault

        vault._load_machine_key_cached.cache_clear()
    except (AttributeError, ImportError):
        pass


@pytest.mark.unit
@pytest.mark.encryption
class TestKeyringFallbackForwardMigration:
    def test_fallback_load_migrates_to_dedicated_kek_on_load(
        self, tmp_path, monkeypatch
    ):
        system_kek = os.urandom(32)
        dedicated_kek = os.urandom(32)
        monkeypatch.setenv("PLEXICHAT_SYSTEM_KEY", _encode_env_key(system_kek))
        monkeypatch.setenv("PLEXICHAT_MESSAGE_KEY", _encode_env_key(dedicated_kek))

        path = tmp_path / "message_keyring.json"
        data = _sample_data()
        _write_keyring(path, system_kek, data)

        kr = Keyring(path, kek_env_var="PLEXICHAT_MESSAGE_KEY")

        assert kr.needs_kek_migration is False
        assert kr._migration_blocked_reason is None
        assert kr.current_version == 2
        assert kr.keys[1] == b"\x11" * 32
        assert kr.keys[2] == b"\x22" * 32
        assert kr.blind_index_root_key == b"\x33" * 32
        assert kr.pepper_keys["v1"] == b"\x44" * 32

        # Persisted under the dedicated KEK (guaranteed on load).
        persisted = _decrypt_keyring(path, dedicated_kek)
        assert persisted["current_version"] == 2
        assert persisted["keys"]["1"] == base64.b64encode(b"\x11" * 32).decode()
        assert (
            persisted["blind_index_root_key"] == base64.b64encode(b"\x33" * 32).decode()
        )
        assert persisted["pepper_keys"]["v1"] == base64.b64encode(b"\x44" * 32).decode()

        # No longer decryptable with the old fallback KEK alone as the stored key.
        with pytest.raises(Exception):
            _decrypt_keyring(path, system_kek)

    def test_migration_is_idempotent(self, tmp_path, monkeypatch):
        system_kek = os.urandom(32)
        dedicated_kek = os.urandom(32)
        monkeypatch.setenv("PLEXICHAT_SYSTEM_KEY", _encode_env_key(system_kek))
        monkeypatch.setenv("PLEXICHAT_MESSAGE_KEY", _encode_env_key(dedicated_kek))

        path = tmp_path / "message_keyring.json"
        _write_keyring(path, system_kek, _sample_data())

        kr = Keyring(path, kek_env_var="PLEXICHAT_MESSAGE_KEY")
        assert not kr.needs_kek_migration

        # Reload: should decrypt with dedicated KEK directly, no fallback/migration.
        kr2 = Keyring(path, kek_env_var="PLEXICHAT_MESSAGE_KEY")
        assert not kr2.needs_kek_migration
        assert kr2._migration_blocked_reason is None
        assert kr2.keys[2] == b"\x22" * 32
        assert kr2.pepper_keys["v1"] == b"\x44" * 32
        # A save() is idempotent and keeps content under dedicated KEK.
        kr2.save()
        persisted = _decrypt_keyring(path, dedicated_kek)
        assert persisted["keys"]["2"] == base64.b64encode(b"\x22" * 32).decode()

    def test_file_keyring_benefits(self, tmp_path, monkeypatch):
        system_kek = os.urandom(32)
        dedicated_kek = os.urandom(32)
        monkeypatch.setenv("PLEXICHAT_SYSTEM_KEY", _encode_env_key(system_kek))
        monkeypatch.setenv("PLEXICHAT_MEDIA_KEY", _encode_env_key(dedicated_kek))

        path = tmp_path / "file_keyring.json"
        _write_keyring(path, system_kek, _sample_data())

        kr = Keyring(path)  # defaults to PLEXICHAT_MEDIA_KEY for file_keyring.json
        assert kr.kek_env_var == "PLEXICHAT_MEDIA_KEY"
        assert not kr.needs_kek_migration
        assert (
            _decrypt_keyring(path, dedicated_kek)["keys"]["1"]
            == base64.b64encode(b"\x11" * 32).decode()
        )

    def test_system_keyring_no_fallback_migration_target(self, tmp_path, monkeypatch):
        system_kek = os.urandom(32)
        monkeypatch.setenv("PLEXICHAT_SYSTEM_KEY", _encode_env_key(system_kek))

        path = tmp_path / "system_keyring.json"
        _write_keyring(path, system_kek, _sample_data())

        kr = Keyring(path)  # system keyring
        assert kr.kek_env_var == "PLEXICHAT_SYSTEM_KEY"
        assert not kr.needs_kek_migration
        assert kr.keys[1] == b"\x11" * 32

    def test_dedicated_kek_unavailable_blocks_writes_but_preserves_recovery(
        self, tmp_path, monkeypatch
    ):
        system_kek = os.urandom(32)
        monkeypatch.setenv("PLEXICHAT_SYSTEM_KEY", _encode_env_key(system_kek))
        monkeypatch.delenv("PLEXICHAT_MESSAGE_KEY", raising=False)
        # Force the dedicated-KEK probe to fail deterministically (no HSM/TPM/local
        # env path should be treated as the dedicated message key in this test).
        from src.utils.encryption.core import keyring as keyring_mod

        orig_get_kek = keyring_mod.Keyring._get_kek

        def _get_kek(self, fallback: bool = False):
            if not fallback and self.kek_env_var == "PLEXICHAT_MESSAGE_KEY":
                raise RuntimeError("dedicated KEK unavailable")
            return orig_get_kek(self, fallback=fallback)

        monkeypatch.setattr(keyring_mod.Keyring, "_get_kek", _get_kek)

        path = tmp_path / "message_keyring.json"
        original = _sample_data()
        _write_keyring(path, system_kek, original)
        original_bytes = path.read_bytes()

        kr = Keyring(path, kek_env_var="PLEXICHAT_MESSAGE_KEY")

        # Recovery read succeeded in memory.
        assert kr.keys[2] == b"\x22" * 32
        assert kr.pepper_keys["v1"] == b"\x44" * 32
        assert kr.needs_kek_migration is True
        assert kr._migration_blocked_reason is not None

        # On-disk keyring unchanged (safe recovery preserved).
        assert path.read_bytes() == original_bytes

        # Unsafe writes refused with a clear error.
        with pytest.raises(KeyringMigrationError) as exc:
            kr.save()
        assert (
            "fallback" in str(exc.value).lower()
            or "migration" in str(exc.value).lower()
        )
        assert "PLEXICHAT_MESSAGE_KEY" in str(exc.value)

        # Still unchanged on disk after refused write.
        assert path.read_bytes() == original_bytes
        assert (
            _decrypt_keyring(path, system_kek)["keys"]["2"]
            == base64.b64encode(b"\x22" * 32).decode()
        )

    def test_write_path_migrates_when_dedicated_kek_becomes_available(
        self, tmp_path, monkeypatch
    ):
        """If load left migration blocked, an eligible write migrates once the
        dedicated KEK becomes available (explicit, no data loss)."""
        system_kek = os.urandom(32)
        dedicated_kek = os.urandom(32)
        monkeypatch.setenv("PLEXICHAT_SYSTEM_KEY", _encode_env_key(system_kek))
        from src.utils.encryption.core import keyring as keyring_mod

        orig_get_kek = keyring_mod.Keyring._get_kek
        allow_dedicated = {"ok": False}

        def _get_kek(self, fallback: bool = False):
            if not fallback and self.kek_env_var == "PLEXICHAT_MESSAGE_KEY":
                if not allow_dedicated["ok"]:
                    raise RuntimeError("dedicated KEK unavailable")
                return dedicated_kek
            return orig_get_kek(self, fallback=fallback)

        monkeypatch.setattr(keyring_mod.Keyring, "_get_kek", _get_kek)

        path = tmp_path / "message_keyring.json"
        _write_keyring(path, system_kek, _sample_data())

        kr = Keyring(path, kek_env_var="PLEXICHAT_MESSAGE_KEY")
        assert kr.needs_kek_migration is True
        with pytest.raises(KeyringMigrationError):
            kr.save()

        # Dedicated KEK becomes available: explicit write performs migration.
        allow_dedicated["ok"] = True
        kr.save()

        assert kr.needs_kek_migration is False
        assert kr._migration_blocked_reason is None
        persisted = _decrypt_keyring(path, dedicated_kek)
        assert persisted["keys"]["1"] == base64.b64encode(b"\x11" * 32).decode()
        assert persisted["keys"]["2"] == base64.b64encode(b"\x22" * 32).decode()
        assert (
            persisted["blind_index_root_key"] == base64.b64encode(b"\x33" * 32).decode()
        )

    def test_non_fallback_load_unaffected(self, tmp_path, monkeypatch):
        dedicated_kek = os.urandom(32)
        monkeypatch.setenv("PLEXICHAT_MESSAGE_KEY", _encode_env_key(dedicated_kek))
        monkeypatch.setenv("PLEXICHAT_SYSTEM_KEY", _encode_env_key(os.urandom(32)))

        path = tmp_path / "message_keyring.json"
        _write_keyring(path, dedicated_kek, _sample_data())

        kr = Keyring(path, kek_env_var="PLEXICHAT_MESSAGE_KEY")
        assert kr.needs_kek_migration is False
        assert kr._migration_blocked_reason is None
        assert kr.keys[2] == b"\x22" * 32
