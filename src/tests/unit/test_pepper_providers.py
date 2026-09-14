"""Tests for pepper primitives: envelope parsing, PIN resolution, providers.

These cover `src.utils.encryption.pepper` without touching Argon2, so they
stand alone from the hashing integration tests in test_password_pepper.py.
"""

import base64
import secrets

import pytest


@pytest.mark.unit
class TestPepperEnvelope:
    def test_parse_roundtrip(self):
        from src.utils.encryption.pepper import parse_envelope, is_peppered

        inner = "$argon2id$v=19$m=8192,t=1,p=1$c2FsdA$aGFzaA"
        assert parse_envelope(f"$plexichat$pepper-v1$pid=v1${inner}") == ("v1", inner)
        assert is_peppered(f"$plexichat$pepper-v1$pid=v2${inner}") is True

    def test_legacy_hashes_are_not_peppered(self):
        from src.utils.encryption.pepper import parse_envelope, is_peppered

        assert parse_envelope("$argon2id$v=19$m=65536,t=3,p=2$cw$dg") is None
        assert is_peppered("not-a-hash") is False
        assert is_peppered("") is False

    def test_malformed_envelope_rejected(self):
        from src.utils.encryption.pepper import parse_envelope

        assert parse_envelope("$plexichat$pepper-v1$pid=has space$x") is None
        assert parse_envelope("$plexichat$pepper-v1$noid$x") is None


@pytest.mark.unit
class TestPinResolution:
    def test_pin_env_wins(self, monkeypatch):
        from src.utils.encryption.pepper import resolve_pin

        monkeypatch.setenv("TEST_HSM_PIN_ENV", "env-pin-value")
        assert (
            resolve_pin({"pin_env": "TEST_HSM_PIN_ENV", "pin": "literal"})
            == "env-pin-value"
        )

    def test_pin_file(self, tmp_path):
        from src.utils.encryption.pepper import resolve_pin

        pin_file = tmp_path / "hsm.pin"
        pin_file.write_text("file-pin-value\n")
        assert resolve_pin({"pin_file": str(pin_file)}) == "file-pin-value"

    def test_pin_expands_env_refs(self, monkeypatch):
        from src.utils.encryption.pepper import resolve_pin

        monkeypatch.setenv("TEST_PIN_REF", "expanded-pin")
        assert resolve_pin({"pin": "${TEST_PIN_REF}"}) == "expanded-pin"
        assert (
            resolve_pin({"pin": "${TEST_PIN_MISSING:-fallback-pin}"}) == "fallback-pin"
        )
        assert resolve_pin({"pin": "literal-pin"}) == "literal-pin"


@pytest.mark.unit
class TestKeyringPepperProvider:
    def test_prehash_stable_within_process(self, tmp_path):
        from src.utils.encryption.pepper import KeyringPepperProvider

        provider = KeyringPepperProvider(
            "v1", keyring_path=str(tmp_path / "system_keyring.json")
        )
        assert provider.prehash("password") == provider.prehash("password")
        assert provider.prehash("password") != provider.prehash("other")

    def test_pepper_shared_across_provider_instances(self, tmp_path):
        from src.utils.encryption.pepper import KeyringPepperProvider

        path = str(tmp_path / "system_keyring.json")
        first = KeyringPepperProvider("v1", keyring_path=path)
        second = KeyringPepperProvider("v1", keyring_path=path)
        assert first.prehash("password") == second.prehash("password")

    def test_ids_are_independent(self, tmp_path):
        from src.utils.encryption.pepper import KeyringPepperProvider

        path = str(tmp_path / "system_keyring.json")
        v1 = KeyringPepperProvider("v1", keyring_path=path)
        v2 = KeyringPepperProvider("v2", keyring_path=path)
        assert v1.prehash("password") != v2.prehash("password")

    def test_key_material_is_32_bytes(self, tmp_path):
        import hmac as hmac_mod
        import hashlib

        from src.utils.encryption.pepper import KeyringPepperProvider

        provider = KeyringPepperProvider(
            "v1", keyring_path=str(tmp_path / "system_keyring.json")
        )
        key = provider._load_key()
        assert len(key) == 32
        expected = base64.b64encode(
            hmac_mod.new(key, b"password", hashlib.sha256).digest()
        ).decode()
        assert provider.prehash("password") == expected

    def test_invalid_id_rejected(self, tmp_path):
        from src.utils.encryption.pepper import KeyringPepperProvider

        with pytest.raises(ValueError):
            KeyringPepperProvider(
                "bad id!", keyring_path=str(tmp_path / "system_keyring.json")
            )


@pytest.mark.unit
class TestBuildProvider:
    def test_keyring_is_default_source(self, tmp_path):
        from src.utils.encryption.pepper import (
            KeyringPepperProvider,
            build_provider,
        )

        provider = build_provider("v1", {"keyring_path": str(tmp_path / "k.json")}, {})
        assert isinstance(provider, KeyringPepperProvider)

    def test_env_source_still_available(self, monkeypatch):
        from src.utils.encryption.pepper import (
            SoftwarePepperProvider,
            build_provider,
        )

        monkeypatch.setenv("TEST_PEPPER_BUILD", secrets.token_hex(32))
        provider = build_provider(
            "v1", {"source": "env", "env_var": "TEST_PEPPER_BUILD"}, {}
        )
        assert isinstance(provider, SoftwarePepperProvider)

    def test_unknown_source_rejected(self):
        from src.utils.encryption.pepper import build_provider

        with pytest.raises(ValueError):
            build_provider("v1", {"source": "vault-teletype"}, {})
