"""
Tests for HardwareVault (Root of Trust / KEK sourcing).

Covers the environment-variable path, machine-local key fallback, key
decoding (hex vs base64), dedicated keyring key files, HSM/TPM init
paths, and the machine-key file loader.
"""

import os
import stat
import sys

import pytest

from src.utils.encryption import vault as vault_module
from src.utils.encryption.vault import HardwareVault

_HEX_KEY = "ab" * 32  # 64 hex chars -> 32 bytes
_B64_KEY = os.urandom(32)


@pytest.fixture(autouse=True)
def _clean_caches_and_env(monkeypatch):
    """Keep the module cache and environment isolated per test."""
    loader = getattr(vault_module, "_load_machine_key_cached", None)
    if loader is not None and hasattr(loader, "cache_clear"):
        loader.cache_clear()
    for var in (
        "PLEXICHAT_SYSTEM_KEY",
        "PLEXICHAT_MESSAGE_KEY",
        "PLEXICHAT_MEDIA_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    yield
    loader = getattr(vault_module, "_load_machine_key_cached", None)
    if loader is not None and hasattr(loader, "cache_clear"):
        loader.cache_clear()


class TestDecodeEnvKey:
    def test_hex_key_decodes_to_32_bytes(self):
        assert vault_module.HardwareVault()._decode_env_key(_HEX_KEY) == bytes.fromhex(
            _HEX_KEY
        )

    def test_base64_key_decodes_to_32_bytes(self):
        encoded = __import__("base64").b64encode(_B64_KEY).decode()
        assert HardwareVault()._decode_env_key(encoded) == _B64_KEY

    def test_hex_takes_precedence_over_base64(self):
        # A 64-char hex string decodes to 48 bytes as base64 but 32 as hex;
        # hex must win.
        assert HardwareVault()._decode_env_key(_HEX_KEY) == bytes.fromhex(_HEX_KEY)

    def test_invalid_key_raises(self):
        with pytest.raises(ValueError, match="32-byte key"):
            HardwareVault()._decode_env_key("not-a-key")

    def test_wrong_length_hex_falls_through_and_raises(self):
        # 62 hex chars -> 31 bytes as hex, 46 bytes as base64: both wrong.
        with pytest.raises(ValueError, match="32-byte key"):
            HardwareVault()._decode_env_key("ab" * 31)


class TestGetKek:
    def test_env_hex_source(self, monkeypatch):
        monkeypatch.setenv("PLEXICHAT_SYSTEM_KEY", _HEX_KEY)
        vault = HardwareVault()
        assert vault.get_kek() == bytes.fromhex(_HEX_KEY)
        assert vault.get_source() == "env"
        assert vault.is_using_secure_source() is True

    def test_env_base64_source(self, monkeypatch):
        encoded = __import__("base64").b64encode(_B64_KEY).decode()
        monkeypatch.setenv("PLEXICHAT_SYSTEM_KEY", encoded)
        vault = HardwareVault()
        assert vault.get_kek() == _B64_KEY
        assert vault.get_source() == "env"

    def test_kek_cached_after_first_call(self, monkeypatch):
        monkeypatch.setenv("PLEXICHAT_SYSTEM_KEY", _HEX_KEY)
        vault = HardwareVault()
        first = vault.get_kek()
        monkeypatch.delenv("PLEXICHAT_SYSTEM_KEY")
        assert vault.get_kek() == first  # cached in _master_key

    def test_machine_key_fallback(self, monkeypatch):
        fixed = b"m" * 32
        monkeypatch.setattr(vault_module, "_load_machine_key_cached", lambda p: fixed)
        vault = HardwareVault()
        assert vault.get_kek() == fixed
        assert vault.get_source() == "local"
        assert vault.is_using_secure_source() is False


class TestMachineKeyPath:
    def test_default_path(self):
        vault = HardwareVault()
        path = vault._get_machine_key_path()
        assert str(path) == os.path.expanduser("~/.plexichat/data/.machine_key")

    def test_dedicated_keyring_path(self, monkeypatch):
        monkeypatch.setenv("PLEXICHAT_MESSAGE_KEY", _HEX_KEY)
        vault = HardwareVault(kek_env_var="PLEXICHAT_MESSAGE_KEY")
        path = vault._get_machine_key_path()
        # "PLEXICHAT_MESSAGE_KEY" -> "plexichat_message" + "_key"
        assert path.name == ".plexichat_message_key"


class TestHardwareInit:
    def test_hsm_disabled_by_default(self):
        assert HardwareVault()._hsm_available is False

    def test_hsm_enabled_but_missing_fields(self):
        import utils.config as config

        config.set("encryption", {"hsm": {"enabled": True}})
        vault = HardwareVault()
        assert vault._hsm_available is False

    def test_hsm_enabled_with_fields_but_library_missing(self):
        import utils.config as config

        config.set(
            "encryption",
            {
                "hsm": {
                    "enabled": True,
                    "library_path": "/nonexistent/lib.so",
                    "slot_id": 0,
                    "pin": "1234",
                }
            },
        )
        vault = HardwareVault()
        assert vault._hsm_available is False

    def test_tpm_unavailable_without_library(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "tpm2_pytss", None)
        vault = HardwareVault()
        assert vault._tpm_available is False


class TestMachineKeyFile:
    def test_loads_existing_key(self, tmp_path):
        key = os.urandom(32)
        key_file = tmp_path / "key"
        key_file.write_bytes(key)
        assert vault_module._load_machine_key_impl(str(key_file)) == key

    def test_generates_and_persists_key(self, tmp_path):
        key_file = tmp_path / "key"
        key = vault_module._load_machine_key_impl(str(key_file))
        assert len(key) == 32
        assert key_file.read_bytes() == key
        mode = stat.S_IMODE(os.stat(key_file).st_mode)
        assert mode == 0o600

    def test_corrupt_key_regenerates(self, tmp_path):
        key_file = tmp_path / "key"
        key_file.write_bytes(b"short")
        key = vault_module._load_machine_key_impl(str(key_file))
        assert len(key) == 32
        assert key_file.read_bytes() == key

    def test_cached_loader_returns_same_key(self, tmp_path):
        key_file = tmp_path / "key"
        key_file.write_bytes(os.urandom(32))
        first = vault_module._load_machine_key_cached(str(key_file))
        second = vault_module._load_machine_key_cached(str(key_file))
        assert first == second
