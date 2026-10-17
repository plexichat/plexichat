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


def _bare_vault(**fields):
    vault = HardwareVault.__new__(HardwareVault)
    vault._master_key = None
    vault._source = "unknown"
    vault._kek_env_var = "PLEXICHAT_SYSTEM_KEY"
    vault._tpm_available = False
    vault._tpm_enabled = True
    vault._tpm_config = {}
    vault._hsm_available = False
    vault._hsm_backend = ""
    vault._hsm_config = {}
    for key, value in fields.items():
        setattr(vault, key, value)
    return vault


class TestTpmBoundKek:
    def test_stable_and_32_bytes(self):
        first = vault_module.tpm_bound_kek(b"public", b"name")
        second = vault_module.tpm_bound_kek(b"public", b"name")
        assert first == second
        assert len(first) == 32

    def test_bound_to_tpm_identity(self):
        base = vault_module.tpm_bound_kek(b"public", b"name")
        assert vault_module.tpm_bound_kek(b"other-public", b"name") != base
        assert vault_module.tpm_bound_kek(b"public", b"other-name") != base
        assert vault_module.tpm_bound_kek(b"public", b"name", label="OTHER") != base

    def test_rejects_empty_inputs(self):
        import pytest as _pytest

        with _pytest.raises(ValueError):
            vault_module.tpm_bound_kek(b"", b"name")
        with _pytest.raises(ValueError):
            vault_module.tpm_bound_kek(b"public", b"")


class _FakeTpmName:
    def __init__(self, buffer):
        self.buffer = buffer


class _FakeTpmPublic:
    def marshal(self):
        return b"fake-tpm-public"


class _FakeTpmCtx:
    def __init__(self, name=b"fake-tpm-name"):
        self._name = name
        self.created = False
        self.flushed = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def create_primary(self, hierarchy, sensitive, public):
        self.created = True
        return ("handle", _FakeTpmPublic(), None, None, None)

    def get_name(self, handle):
        return _FakeTpmName(self._name)

    def flush_context(self, handle):
        self.flushed.append(handle)

    def evict_control(self, hierarchy, handle, persistent):
        return None


def _install_fake_tpm(monkeypatch, name=b"fake-tpm-name"):
    import types

    ctx_holder = {}

    def _ctx_factory():
        ctx = _FakeTpmCtx(name=name)
        ctx_holder["ctx"] = ctx
        return ctx

    fake = types.ModuleType("tpm2_pytss")
    fake.ESYS_Context = _ctx_factory

    class _Public:
        @staticmethod
        def parse(alg, objectAttributes):
            return ("in-public", alg)

    fake.TPM2B_PUBLIC = _Public
    fake.TPM2B_SENSITIVE_CREATE = lambda: ("sensitive",)  # noqa: E731

    consts = types.ModuleType("tpm2_pytss.constants")

    class _TR:
        OWNER = "OWNER"

    consts.ESYS_TR = _TR
    consts.TPMA_OBJECT = type(
        "TPMA",
        (),
        {
            "USERWITHAUTH": 1,
            "RESTRICTED": 2,
            "DECRYPT": 4,
            "FIXEDTPM": 8,
            "FIXEDPARENT": 16,
            "SENSITIVEDATAORIGIN": 32,
        },
    )
    monkeypatch.setitem(sys.modules, "tpm2_pytss", fake)
    monkeypatch.setitem(sys.modules, "tpm2_pytss.constants", consts)
    return ctx_holder


class TestTpmKeyDerivation:
    def test_derives_bound_kek(self, monkeypatch):
        _install_fake_tpm(monkeypatch, name=b"tpm-A")
        vault = _bare_vault(_tpm_available=True, _tpm_config={})
        assert vault._get_tpm_key() == vault_module.tpm_bound_kek(
            b"fake-tpm-public", b"tpm-A"
        )

    def test_different_tpms_different_keks(self, monkeypatch):
        _install_fake_tpm(monkeypatch, name=b"tpm-A")
        vault = _bare_vault(_tpm_available=True, _tpm_config={})
        kek_a = vault._get_tpm_key()
        _install_fake_tpm(monkeypatch, name=b"tpm-B")
        assert vault._get_tpm_key() != kek_a

    def test_label_config_changes_kek(self, monkeypatch):
        _install_fake_tpm(monkeypatch)
        vault = _bare_vault(
            _tpm_available=True,
            _tpm_config={"label": "ROTATED", "salt": "plexichat:tpm-kek:v2"},
        )
        assert vault._get_tpm_key() == vault_module.tpm_bound_kek(
            b"fake-tpm-public",
            b"fake-tpm-name",
            label="ROTATED",
            salt="plexichat:tpm-kek:v2",
        )

    def test_disabled_tpm_never_probes_hardware(self, monkeypatch):
        monkeypatch.setattr(
            HardwareVault, "_tpm_config_dict", lambda self: {"enabled": False}
        )
        monkeypatch.setitem(sys.modules, "tpm2_pytss", None)
        vault = _bare_vault()
        vault._init_tpm()
        assert vault._tpm_enabled is False
        assert vault._tpm_available is False

    def test_unavailable_tpm_returns_none(self):
        assert _bare_vault(_tpm_available=False)._get_tpm_key() is None


class _FakePkcs11Key:
    def __init__(self, value=None):
        self._value = value

    def __getitem__(self, attr):
        if attr == "CKA_VALUE":
            if self._value is None:
                raise KeyError("non-extractable")
            return self._value
        raise KeyError(attr)


class _FakePkcs11Session:
    def __init__(self, token, key=b"K" * 32, extractable=True):
        self._token = token
        self._key = _FakePkcs11Key(key if extractable else None)
        self._store = key

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_objects(self, template):
        return [self._key]

    def generate_key(self, *args, **kwargs):
        template = kwargs.get("template", {})
        self._token.generated_template = template
        return self._key

    def encrypt(self, key, data, mechanism=None):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        iv = getattr(mechanism, "iv", None) or b"i" * 12
        aad = getattr(mechanism, "aad", None) or b""
        return AESGCM(self._store).encrypt(iv, data, aad or None)

    def decrypt(self, key, data, mechanism=None):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        iv = getattr(mechanism, "iv", None) or b"i" * 12
        aad = getattr(mechanism, "aad", None) or b""
        return AESGCM(self._store).decrypt(iv, data, aad or None)


class _FakePkcs11Token:
    def __init__(self, slot_id=0, key=b"K" * 32, extractable=True):
        self.slot_id = slot_id
        self.generated_template = None
        self._key = key
        self._extractable = extractable

    def open(self, user_pin=None, rw=False):
        assert user_pin == "pin"
        return _FakePkcs11Session(self, self._key, self._extractable)


class _FakePkcs11Lib:
    def __init__(self, token):
        self._token = token

    def get_tokens(self, token_label=None):
        return [self._token]


def _install_fake_pkcs11(monkeypatch, key=b"K" * 32, extractable=True):
    import types

    token = _FakePkcs11Token(key=key, extractable=extractable)
    fake = types.ModuleType("pkcs11")

    class _Gcm:
        def __init__(self, iv, aad=None, tag_bits=128):
            self.iv = bytes(iv)
            self.aad = bytes(aad or b"")

    fake.Mechanism = type("Mechanism", (), {"AES_GCM": _Gcm, "AES_KEY_GEN": "gen"})
    fake.KeyType = type("KeyType", (), {"AES": "AES"})
    fake.lib = lambda path: _FakePkcs11Lib(token)  # noqa: E731
    monkeypatch.setitem(sys.modules, "pkcs11", fake)
    return token


class TestHsmPkcs11Backend:
    def test_retrieves_extractable_key(self, monkeypatch):
        _install_fake_pkcs11(monkeypatch, key=b"E" * 32, extractable=True)
        vault = _bare_vault(
            _hsm_available=True,
            _hsm_backend="pkcs11",
            _hsm_config={
                "library_path": "/fake/softhsm.so",
                "slot_id": 0,
                "key_label": "plexichat_kek",
            },
        )
        assert vault._get_hsm_key_pkcs11("pin") == b"E" * 32

    def test_non_extractable_refuses_export(self, monkeypatch):
        _install_fake_pkcs11(monkeypatch, extractable=False)
        vault = _bare_vault(
            _hsm_available=True,
            _hsm_backend="pkcs11",
            _hsm_config={
                "library_path": "/fake/softhsm.so",
                "slot_id": 0,
                "key_label": "plexichat_kek",
            },
        )
        assert vault._get_hsm_key_pkcs11("pin") is None

    def test_dispatch_prefers_pkcs11_over_pykcs11(self, monkeypatch):
        _install_fake_pkcs11(monkeypatch, key=b"P" * 32)
        monkeypatch.setitem(sys.modules, "PyKCS11", None)
        vault = _bare_vault(
            _hsm_available=True,
            _hsm_backend="",
            _hsm_config={
                "library_path": "/fake/softhsm.so",
                "slot_id": 0,
                "key_label": "plexichat_kek",
                "pin": "pin",
            },
        )
        assert vault._get_hsm_key() == b"P" * 32

    def test_hsm_crypto_roundtrip(self, monkeypatch):
        _install_fake_pkcs11(monkeypatch, key=b"H" * 32)
        vault = _bare_vault(
            _hsm_available=True,
            _hsm_backend="pkcs11",
            _hsm_config={
                "library_path": "/fake/softhsm.so",
                "slot_id": 0,
                "key_label": "plexichat_kek",
                "pin": "pin",
            },
        )
        envelope = vault.hsm_encrypt(b"secret-bytes", aad=b"ctx")
        assert envelope.startswith(vault_module.HSM_ENVELOPE_PREFIX)
        assert vault.hsm_decrypt(envelope, aad=b"ctx") == b"secret-bytes"

    def test_hsm_crypto_rejects_wrong_aad(self, monkeypatch):
        _install_fake_pkcs11(monkeypatch, key=b"H" * 32)
        vault = _bare_vault(
            _hsm_available=True,
            _hsm_backend="pkcs11",
            _hsm_config={
                "library_path": "/fake/softhsm.so",
                "slot_id": 0,
                "key_label": "plexichat_kek",
                "pin": "pin",
            },
        )
        envelope = vault.hsm_encrypt(b"secret-bytes", aad=b"ctx")
        with __import__("pytest").raises(vault_module.HsmCryptoError):
            vault.hsm_decrypt(envelope, aad=b"other")


class _FakePyKCS11Session:
    def __init__(self, ns, key=b"Y" * 32, extractable=True):
        self._ns = ns
        self._key = key
        self._extractable = extractable
        self.created = None

    def findObjects(self, template):
        if getattr(self, "_missing", False):
            return []
        return ["key-handle"]

    def getAttributeValue(self, _handle, _attrs):
        ns = self._ns
        flag = ns.CK_TRUE if self._extractable else ns.CK_FALSE
        return [flag, self._key]

    def createObject(self, template):
        self.created = template
        return "new-handle"

    def encrypt(self, _handle, data, _mech):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        iv, aad, _bits = _mech
        return AESGCM(self._key).encrypt(bytes(iv), data, bytes(aad) or None)

    def decrypt(self, _handle, data, _mech):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        iv, aad, _bits = _mech
        return AESGCM(self._key).decrypt(bytes(iv), data, bytes(aad) or None)

    def login(self, _pin):
        return None

    def logout(self):
        return None

    def closeSession(self):
        return None


def _install_fake_pykcs11(monkeypatch, key=b"Y" * 32, extractable=True):
    import types

    session = _FakePyKCS11Session(None, key=key, extractable=extractable)
    fake_top = types.ModuleType("PyKCS11")
    ns = types.SimpleNamespace(
        CKA_LABEL="label",
        CKA_CLASS="class",
        CKO_SECRET_KEY="secret",
        CKA_KEY_TYPE="kt",
        CKK_AES="aes",
        CKA_VALUE_LEN="len",
        CKA_VALUE="value",
        CKA_ENCRYPT="enc",
        CKA_DECRYPT="dec",
        CKA_EXTRACTABLE="ext",
        CKA_PRIVATE="priv",
        CKA_TOKEN="tok",
        CKA_SENSITIVE="sens",
        CK_TRUE="true",
        CK_FALSE="false",
        CKF_SERIAL_SESSION=1,
        CKF_RW_SESSION=2,
        CKM_AES_GCM="gcm",
        Mechanism=lambda mech, param: param,
    )
    session._ns = ns

    class _Lib:
        def load(self, _path):
            return None

        def openSession(self, _slot, _flags):
            return session

    ns.PyKCS11Lib = _Lib
    fake_top.PyKCS11 = ns
    monkeypatch.setitem(sys.modules, "PyKCS11", fake_top)
    # Ensure the modern backend is NOT importable so the legacy path runs.
    real_import = __import__("importlib").import_module

    def _guarded(name, *args, **kwargs):
        if name == "pkcs11":
            raise ImportError("no pkcs11 in this test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(vault_module.importlib, "import_module", _guarded)
    return session


class TestHsmPyKCS11Backend:
    def test_retrieves_extractable_key(self, monkeypatch):
        _install_fake_pykcs11(monkeypatch, key=b"Y" * 32, extractable=True)
        vault = _bare_vault(
            _hsm_available=True,
            _hsm_config={
                "library_path": "/fake/lib.so",
                "slot_id": 0,
                "key_label": "plexichat_kek",
            },
        )
        assert vault._get_hsm_key_pykcs11("pin") == b"Y" * 32

    def test_non_extractable_refuses_export(self, monkeypatch):
        _install_fake_pykcs11(monkeypatch, extractable=False)
        vault = _bare_vault(
            _hsm_available=True,
            _hsm_config={
                "library_path": "/fake/lib.so",
                "slot_id": 0,
                "key_label": "plexichat_kek",
            },
        )
        assert vault._get_hsm_key_pykcs11("pin") is None

    def test_allow_generate_false_raises(self, monkeypatch):
        session = _install_fake_pykcs11(monkeypatch)
        session._missing = True
        vault = _bare_vault(
            _hsm_available=True,
            _hsm_config={
                "library_path": "/fake/lib.so",
                "slot_id": 0,
                "key_label": "plexichat_kek",
                "allow_generate": False,
            },
        )
        with __import__("pytest").raises(vault_module.HsmCryptoError):
            vault._get_hsm_key_pykcs11("pin")

    def test_hsm_crypto_roundtrip(self, monkeypatch):
        _install_fake_pykcs11(monkeypatch, key=b"Z" * 32)
        vault = _bare_vault(
            _hsm_available=True,
            _hsm_backend="pykcs11",
            _hsm_config={
                "library_path": "/fake/lib.so",
                "slot_id": 0,
                "key_label": "plexichat_kek",
                "pin": "pin",
            },
        )
        envelope = vault.hsm_encrypt(b"payload", aad=b"aad")
        assert envelope.startswith(vault_module.HSM_ENVELOPE_PREFIX)
        assert vault.hsm_decrypt(envelope, aad=b"aad") == b"payload"


class TestHardwareStatus:
    def test_status_has_no_secrets(self):
        vault = _bare_vault(
            _hsm_available=True, _hsm_backend="pkcs11", _tpm_available=True
        )
        status = vault.get_hardware_status()
        assert status["hsm_available"] is True
        assert status["hsm_backend"] == "pkcs11"
        assert status["tpm_available"] is True
        assert "key" not in str(status).lower() or "kek_source" in status
        assert "H" * 8 not in str(status.values())
