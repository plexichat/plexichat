"""Additional hardening/split-compat tests for the vault package."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from src.utils.encryption import vault as vault_module
from src.utils.encryption.vault import (
    HSM_ENVELOPE_PREFIX,
    HsmCryptoError,
    HardwareVault,
    VaultPolicyError,
    _parse_hsm_envelope,
    tpm_bound_kek,
    vault,
)

_HEX_KEY = "76f1a9aeb10f30f7ac17f40aa642f8f57a001e2fbbf3b8cacc6662e678d190e5"


@pytest.fixture(autouse=True)
def _clean_caches_and_env(monkeypatch):
    loader = getattr(vault_module, "_load_machine_key_cached", None)
    if loader is not None and hasattr(loader, "cache_clear"):
        loader.cache_clear()
    for var in ("PLEXICHAT_SYSTEM_KEY", "PLEXICHAT_MESSAGE_KEY", "PLEXICHAT_MEDIA_KEY"):
        monkeypatch.delenv(var, raising=False)
    yield
    loader = getattr(vault_module, "_load_machine_key_cached", None)
    if loader is not None and hasattr(loader, "cache_clear"):
        loader.cache_clear()


def _bare_vault(**fields):
    obj = HardwareVault.__new__(HardwareVault)
    obj._master_key = None
    obj._source = "unknown"
    obj._kek_env_var = "PLEXICHAT_SYSTEM_KEY"
    obj._tpm_available = False
    obj._tpm_enabled = True
    obj._tpm_config = {}
    obj._hsm_available = False
    obj._hsm_backend = ""
    obj._hsm_config = {}
    for key, value in fields.items():
        setattr(obj, key, value)
    return obj


@pytest.mark.unit
class TestVaultPackageApi:
    def test_public_exports_and_singleton(self):
        assert vault_module.HardwareVault is HardwareVault
        assert vault_module.HsmCryptoError is HsmCryptoError
        assert vault_module.HSM_ENVELOPE_PREFIX == HSM_ENVELOPE_PREFIX
        assert callable(vault_module._load_machine_key_impl)
        assert callable(vault_module._load_machine_key_cached)
        assert vault_module.vault is vault
        assert isinstance(vault, HardwareVault)

    def test_import_paths_resolve(self):
        import src.utils.encryption.vault as pkg_a
        import utils.encryption.vault as pkg_b

        assert pkg_a.tpm_bound_kek(b"a", b"b") == pkg_b.tpm_bound_kek(b"a", b"b")
        assert pkg_a.HSM_ENVELOPE_PREFIX == pkg_b.HSM_ENVELOPE_PREFIX
        assert pkg_a.HardwareVault.__name__ == pkg_b.HardwareVault.__name__

    def test_module_has_importlib_for_test_patching(self):
        assert vault_module.importlib is sys.modules["importlib"]

    def test_parse_hsm_envelope_rejects_bad_prefix_and_base64(self):
        with pytest.raises(HsmCryptoError, match="not an HSM envelope"):
            _parse_hsm_envelope("BAD:abc")
        with pytest.raises(HsmCryptoError, match="malformed HSM envelope"):
            _parse_hsm_envelope(HSM_ENVELOPE_PREFIX + "!!!")

    def test_parse_hsm_envelope_rejects_too_short(self):
        import base64

        blob = base64.b64encode(b"short").decode("ascii")
        with pytest.raises(HsmCryptoError, match="too short"):
            _parse_hsm_envelope(HSM_ENVELOPE_PREFIX + blob)


@pytest.mark.unit
class TestMachineKeyHardening:
    def test_refuses_symlink(self, tmp_path):
        real = tmp_path / "real"
        real.write_bytes(os.urandom(32))
        link = tmp_path / "link"
        link.symlink_to(real)
        with pytest.raises(RuntimeError, match="Unsafe machine key file"):
            vault_module._load_machine_key_impl(str(link))

    def test_refuses_directory(self, tmp_path):
        d = tmp_path / "dirkey"
        d.mkdir()
        with pytest.raises(RuntimeError, match="Unsafe machine key file"):
            vault_module._load_machine_key_impl(str(d))

    def test_corrupt_length_regenerates(self, tmp_path):
        key_file = tmp_path / "key"
        key_file.write_bytes(b"short")
        key = vault_module._load_machine_key_impl(str(key_file))
        assert len(key) == 32
        assert key_file.read_bytes() == key

    def test_generated_key_has_secure_mode(self, tmp_path):
        key_file = tmp_path / "key"
        vault_module._load_machine_key_impl(str(key_file))
        mode = os.stat(key_file).st_mode & 0o777
        assert mode == 0o600

    def test_machine_key_path_helper(self):
        from src.utils.encryption.vault.machine_key import get_machine_key_path

        assert (
            get_machine_key_path("PLEXICHAT_SYSTEM_KEY")
            == Path.home() / ".plexichat" / "data" / ".machine_key"
        )

    def test_dedicated_machine_key_path_requires_env(self, monkeypatch):
        from src.utils.encryption.vault.machine_key import get_machine_key_path

        monkeypatch.delenv("PLEXICHAT_MESSAGE_KEY", raising=False)
        assert get_machine_key_path("PLEXICHAT_MESSAGE_KEY").name == ".machine_key"
        monkeypatch.setenv("PLEXICHAT_MESSAGE_KEY", _HEX_KEY)
        assert (
            get_machine_key_path("PLEXICHAT_MESSAGE_KEY").name
            == ".plexichat_message_key"
        )


@pytest.mark.unit
class TestPolicyAndStatus:
    def test_hardware_required_blocks_local_fallback(self, monkeypatch):
        monkeypatch.setattr(vault_module.config, "hardware_required", lambda: True)
        monkeypatch.setattr(
            "src.utils.encryption.vault.hardware_vault.hardware_required", lambda: True
        )
        monkeypatch.setattr(
            "src.utils.encryption.vault.config.hardware_required", lambda: True
        )
        obj = _bare_vault()
        with pytest.raises(VaultPolicyError, match="hardware-rooted KEK"):
            obj.get_kek()

    def test_status_shape_and_source_policy(self, monkeypatch):
        monkeypatch.setenv("PLEXICHAT_SYSTEM_KEY", _HEX_KEY)
        obj = HardwareVault()
        status = obj.get_hardware_status()
        assert set(status) == {
            "kek_source",
            "hsm_available",
            "hsm_backend",
            "hsm_managed_crypto",
            "tpm_enabled",
            "tpm_available",
        }
        policy = obj.describe_source_policy()
        assert policy["secure_source"] is True
        assert policy["hardware_rooted"] is False
        assert policy["kek_source"] == "env"

    def test_decode_env_key_delegates_and_validates(self):
        assert HardwareVault()._decode_env_key(_HEX_KEY) == bytes.fromhex(_HEX_KEY)
        with pytest.raises(ValueError):
            HardwareVault()._decode_env_key("nope")


@pytest.mark.unit
class TestHsmTpmHelpers:
    def test_tpm_bound_kek_input_validation(self):
        assert len(tpm_bound_kek(b"p", b"n", label="L", salt="s")) == 32
        with pytest.raises(ValueError):
            tpm_bound_kek(b"", b"n")
        with pytest.raises(ValueError):
            tpm_bound_kek(b"p", b"", label="")

    def test_supports_hsm_crypto_requires_library(self):
        obj = _bare_vault(_hsm_available=True, _hsm_config={})
        assert obj.supports_hsm_crypto() is False
        obj = _bare_vault(_hsm_available=True, _hsm_config={"library_path": "/x"})
        assert obj.supports_hsm_crypto() is True

    def test_hsm_encrypt_requires_available(self):
        with pytest.raises(HsmCryptoError, match="not available"):
            _bare_vault(_hsm_available=False).hsm_encrypt(b"x")

    def test_hsm_decrypt_requires_available(self):
        with pytest.raises(HsmCryptoError, match="not available"):
            _bare_vault(_hsm_available=False).hsm_decrypt(HSM_ENVELOPE_PREFIX + "AA==")

    def test_pykcs11_mechanism_builder(self):
        from src.utils.encryption.vault.hsm_pykcs11 import gcm_mechanism

        class _Py:
            CKM_AES_GCM = 1

            @staticmethod
            def Mechanism(kind, param):
                return (kind, param)

        mech = gcm_mechanism(_Py, b"i" * 12, b"aad")
        assert mech[0] == 1

        class _Bad:
            CKM_AES_GCM = None

        with pytest.raises(HsmCryptoError, match="lacks CKM_AES_GCM"):
            gcm_mechanism(_Bad, b"i" * 12, b"")

    def test_tpm_marshal_and_name_helpers(self):
        obj = HardwareVault

        class _Pub:
            def marshal(self):
                return b"pub"

        assert obj._tpm_marshal_public(_Pub()) == b"pub"

        class _Ctx:
            @staticmethod
            def get_name(_h):
                return type("N", (), {"buffer": b"name"})()

        assert obj._tpm_object_name(_Ctx(), "h", object()) == b"name"
