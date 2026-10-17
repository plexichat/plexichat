"""Tests for versioned password peppering (software providers + envelope)."""

import base64
import os
import secrets

import pytest


@pytest.fixture(autouse=True)
def _isolate_pepper_store():
    from src.utils.encryption import pepper as pepper_mod

    pepper_mod.reset_pepper_store()
    yield
    pepper_mod.reset_pepper_store()


def _make_manager_with_pepper(monkeypatch, peppers, current_id="v1"):
    import utils.config as config_mod

    monkeypatch.setattr(
        config_mod,
        "get",
        lambda key, default=None: (
            {
                "password_pepper": {
                    "enabled": True,
                    "current_id": current_id,
                    "peppers": peppers,
                },
                "hsm": {},
            }
            if key == "encryption"
            else (default if default is not None else {})
        ),
    )
    from src.utils.encryption import pepper as pepper_mod

    pepper_mod.reset_pepper_store()
    from src.utils.encryption.core import EncryptionManager

    mgr = EncryptionManager(
        argon2_time_cost=1, argon2_memory_cost=8192, argon2_parallelism=1
    )
    return mgr, pepper_mod


@pytest.mark.unit
class TestPepperedHashing:
    def test_peppered_roundtrip(self, monkeypatch):
        key = secrets.token_hex(32)
        monkeypatch.setenv("TEST_PEPPER_V1", key)
        mgr, _ = _make_manager_with_pepper(
            monkeypatch,
            {"v1": {"source": "env", "env_var": "TEST_PEPPER_V1"}},
        )
        h = mgr.hash_password("CorrectHorse123!")
        assert h.startswith("$plexichat$pepper-v1$pid=v1$$argon2id$")
        assert mgr.verify_password("CorrectHorse123!", h) is True
        assert mgr.verify_password("correcthorse123!", h) is False
        assert mgr.needs_rehash(h) is False

    def test_unpeppered_hash_never_verifies(self, monkeypatch):
        key = secrets.token_hex(32)
        monkeypatch.setenv("TEST_PEPPER_V1", key)
        mgr, _ = _make_manager_with_pepper(
            monkeypatch,
            {"v1": {"source": "env", "env_var": "TEST_PEPPER_V1"}},
        )
        # A raw Argon2id hash (the pre-pepper format) is rejected even for
        # the right password, and reports no upgrade path: fail closed.
        raw = mgr.password_hasher.hash("SomePassword123!")
        assert not raw.startswith("$plexichat$")
        assert mgr.verify_password("SomePassword123!", raw) is False
        assert mgr.needs_rehash(raw) is False
        assert mgr.get_password_pepper_id(raw) is None

    def test_rotation_old_hash_still_verifies_but_rehashes(self, monkeypatch):
        k1, k2 = secrets.token_hex(32), secrets.token_hex(32)
        monkeypatch.setenv("TEST_PEPPER_V1", k1)
        monkeypatch.setenv("TEST_PEPPER_V2", k2)
        mgr_v1, pepper_mod = _make_manager_with_pepper(
            monkeypatch,
            {"v1": {"source": "env", "env_var": "TEST_PEPPER_V1"}},
            current_id="v1",
        )
        old_hash = mgr_v1.hash_password("RotateMe123!")

        pepper_mod.reset_pepper_store()
        mgr_v2, _ = _make_manager_with_pepper(
            monkeypatch,
            {
                "v1": {"source": "env", "env_var": "TEST_PEPPER_V1"},
                "v2": {"source": "env", "env_var": "TEST_PEPPER_V2"},
            },
            current_id="v2",
        )
        assert mgr_v2.verify_password("RotateMe123!", old_hash) is True
        assert mgr_v2.needs_rehash(old_hash) is True
        assert mgr_v2.get_password_pepper_id(old_hash) == "v1"

    def test_wrong_password_fails_closed(self, monkeypatch):
        key = secrets.token_hex(32)
        monkeypatch.setenv("TEST_PEPPER_V1", key)
        mgr, _ = _make_manager_with_pepper(
            monkeypatch,
            {"v1": {"source": "env", "env_var": "TEST_PEPPER_V1"}},
        )
        h = mgr.hash_password("RightPassword123!")
        assert mgr.verify_password("WrongPassword123!", h) is False
        assert mgr.verify_password("", h) is False
        assert mgr.verify_password("RightPassword123!", "not-a-hash") is False

    def test_peppered_hash_without_pepper_config_fails_closed(self, monkeypatch):
        key = secrets.token_hex(32)
        monkeypatch.setenv("TEST_PEPPER_V1", key)
        mgr, pepper_mod = _make_manager_with_pepper(
            monkeypatch,
            {"v1": {"source": "env", "env_var": "TEST_PEPPER_V1"}},
        )
        h = mgr.hash_password("Secret123!")
        # Now disable peppering entirely: peppered hash must not verify.
        pepper_mod.reset_pepper_store()
        import utils.config as config_mod

        monkeypatch.setattr(
            config_mod, "get", lambda k, d=None: d if d is not None else {}
        )
        assert mgr.verify_password("Secret123!", h) is False

    def test_file_source(self, monkeypatch, tmp_path):
        key_b64 = base64.b64encode(secrets.token_bytes(32)).decode()
        f = tmp_path / "pepper.key"
        f.write_text(key_b64 + "\n")
        os.chmod(f, 0o600)
        mgr, _ = _make_manager_with_pepper(
            monkeypatch, {"v1": {"source": "file", "file_path": str(f)}}
        )
        h = mgr.hash_password("FilePepper123!")
        assert mgr.verify_password("FilePepper123!", h) is True
