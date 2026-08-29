"""
Device management tests for auth module.
"""

import pytest
from src.core.auth.exceptions import TokenInvalidError
from unittest.mock import patch


class TestDevices:
    """Test device management."""

    def test_login_creates_device(self, db, auth_manager):
        """Test login with device info creates device record."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="device_test",
                email="device_test@example.com",
                password="TestPass123!",
            )

        fp = f"device_{user.id}_create"
        with patch.object(encryption, "verify_password", return_value=True):
            auth_manager.login(
                "device_test",
                "TestPass123!",
                device_info={
                    "fingerprint": fp,
                    "name": "Test Device",
                    "type": "desktop",
                },
            )

        devices = auth_manager.get_devices(user.id)
        assert any(d.fingerprint == fp for d in devices)

    def test_same_fingerprint_reuses_device(self, db, auth_manager):
        """Test same fingerprint reuses device record."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="same_device_test",
                email="same_device_test@example.com",
                password="TestPass123!",
            )

        fp = f"same_device_{user.id}"
        with patch.object(encryption, "verify_password", return_value=True):
            auth_manager.login(
                "same_device_test", "TestPass123!", device_info={"fingerprint": fp}
            )
            auth_manager.login(
                "same_device_test", "TestPass123!", device_info={"fingerprint": fp}
            )

        devices = auth_manager.get_devices(user.id)
        same_fp = [d for d in devices if d.fingerprint == fp]
        assert len(same_fp) == 1

    def test_different_fingerprint_creates_new(self, db, auth_manager):
        """Test different fingerprint creates new device."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="diff_device_test",
                email="diff_device_test@example.com",
                password="TestPass123!",
            )

        with patch.object(encryption, "verify_password", return_value=True):
            auth_manager.login(
                "diff_device_test",
                "TestPass123!",
                device_info={"fingerprint": f"dev1_{user.id}"},
            )
            auth_manager.login(
                "diff_device_test",
                "TestPass123!",
                device_info={"fingerprint": f"dev2_{user.id}"},
            )

        devices = auth_manager.get_devices(user.id)
        assert len(devices) >= 2

    def test_get_devices(self, db, auth_manager):
        """Test getting user devices."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="get_devices_test",
                email="get_devices_test@example.com",
                password="TestPass123!",
            )

        with patch.object(encryption, "verify_password", return_value=True):
            auth_manager.login(
                "get_devices_test",
                "TestPass123!",
                device_info={"fingerprint": f"fp1_{user.id}"},
            )
            auth_manager.login(
                "get_devices_test",
                "TestPass123!",
                device_info={"fingerprint": f"fp2_{user.id}"},
            )

        devices = auth_manager.get_devices(user.id)
        assert len(devices) >= 2

    def test_rename_device(self, db, auth_manager):
        """Test renaming a device."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="rename_device_test",
                email="rename_device_test@example.com",
                password="TestPass123!",
            )

        fp = f"rename_fp_{user.id}"
        with patch.object(encryption, "verify_password", return_value=True):
            auth_manager.login(
                "rename_device_test",
                "TestPass123!",
                device_info={"fingerprint": fp, "name": "Old Name"},
            )

        devices = auth_manager.get_devices(user.id)
        device = next(d for d in devices if d.fingerprint == fp)

        result = auth_manager.rename_device(user.id, device.id, "New Name")
        assert result is True

    def test_rename_device_wrong_user(self, db, auth_manager):
        """Test user cannot rename another user's device."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="dev1_test",
                email="dev1_test@example.com",
                password="TestPass123!",
            )
            user2 = auth_manager.register(
                username="dev2_test",
                email="dev2_test@example.com",
                password="TestPass123!",
            )

        with patch.object(encryption, "verify_password", return_value=True):
            auth_manager.login(
                "dev1_test", "TestPass123!", device_info={"fingerprint": "user1_device"}
            )

        devices = auth_manager.get_devices(user1.id)
        device = devices[0]

        result = auth_manager.rename_device(user2.id, device.id, "Hacked")
        assert result is False

    def test_revoke_device(self, db, auth_manager):
        """Test revoking a device."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="revoke_device_test",
                email="revoke_device_test@example.com",
                password="TestPass123!",
            )

        fp = f"revoke_fp_{user.id}"
        with patch.object(encryption, "verify_password", return_value=True):
            auth_manager.login(
                "revoke_device_test", "TestPass123!", device_info={"fingerprint": fp}
            )

        devices = auth_manager.get_devices(user.id)
        device = next(d for d in devices if d.fingerprint == fp)

        result = auth_manager.revoke_device(user.id, device.id)
        assert result is True

        devices_after = auth_manager.get_devices(user.id)
        assert not any(d.fingerprint == fp for d in devices_after)

    def test_revoke_device_logs_out_sessions(self, db, auth_manager):
        """Test revoking a device invalidates all its sessions."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="revdev_test",
                email="revdev_test@example.com",
                password="TestPass123!",
            )

        with patch.object(encryption, "verify_password", return_value=True):
            result = auth_manager.login(
                "revdev_test",
                "TestPass123!",
                device_info={"fingerprint": "session_device"},
            )
            token = result.token

        devices = auth_manager.get_devices(user.id)
        device = next(d for d in devices if d.fingerprint == "session_device")

        auth_manager.revoke_device(user.id, device.id)

        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token(token)

    def test_device_type_recorded(self, db, auth_manager):
        """Test device type is recorded."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="type_device_test",
                email="type_device_test@example.com",
                password="TestPass123!",
            )

        fp = f"type_fp_{user.id}"
        with patch.object(encryption, "verify_password", return_value=True):
            auth_manager.login(
                "type_device_test",
                "TestPass123!",
                device_info={"fingerprint": fp, "type": "mobile"},
            )

        devices = auth_manager.get_devices(user.id)
        device = next(d for d in devices if d.fingerprint == fp)

        assert device.device_type == "mobile"


class TestTrustedDevices:
    """Trusted-device (2FA skip) tests."""

    def _mgr(self, auth_manager):
        """The fixture returns the auth module; tests need the manager instance."""
        from src.core.auth._lazy import _get_auth_manager

        return _get_auth_manager().get_instance()

    def _register_and_login_with_device(self, auth_manager, username: str, fp: str):
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username=username,
                email=f"{username}@example.com",
                password="TestPass123!",
            )
        with patch.object(encryption, "verify_password", return_value=True):
            auth_manager.login(
                username, "TestPass123!", device_info={"fingerprint": fp}
            )
        return user

    def test_trust_unknown_fingerprint_is_noop(self, db, auth_manager):
        """Trusting a fingerprint never seen before must not create trust."""
        user = self._register_and_login_with_device(
            auth_manager, "trust_unknown", "known_fp"
        )
        assert auth_manager.trust_device(user.id, "never_seen_fp_123") is False
        assert auth_manager.is_trusted_device(user.id, "never_seen_fp_123") is False

    def test_trust_then_login_skips_2fa(self, db, auth_manager):
        """A trusted device with TOTP enabled must log in directly (no challenge)."""
        from src.utils import encryption

        user = self._register_and_login_with_device(
            auth_manager, "trust_skip", "trusted_fp"
        )

        # Enable TOTP directly (setup + confirm path needs a valid code; flip the
        # flags the same way confirm_2fa would after a successful verification).
        self._mgr(auth_manager)._db.execute(
            "UPDATE auth_users SET totp_enabled = 1, totp_secret_encrypted = ? WHERE id = ?",
            ("x" * 32, user.id),
        )

        # Before trusting: login requires 2FA.
        with patch.object(encryption, "verify_password", return_value=True):
            result = auth_manager.login(
                "trust_skip", "TestPass123!", device_info={"fingerprint": "trusted_fp"}
            )
        assert result.status.value == "2fa_required"

        # Mark the device trusted, then login again: no challenge, direct session.
        assert auth_manager.trust_device(user.id, "trusted_fp") is True
        with patch.object(encryption, "verify_password", return_value=True):
            result2 = auth_manager.login(
                "trust_skip", "TestPass123!", device_info={"fingerprint": "trusted_fp"}
            )
        assert result2.status.value == "success"
        assert result2.token

    def test_missing_fingerprint_never_trusted(self, db, auth_manager):
        """Login without a fingerprint must still require 2FA even if some
        device is trusted (an attacker stripping the header gains nothing)."""
        from src.utils import encryption

        user = self._register_and_login_with_device(
            auth_manager, "trust_nofp", "nofp_device"
        )
        self._mgr(auth_manager)._db.execute(
            "UPDATE auth_users SET totp_enabled = 1, totp_secret_encrypted = ? WHERE id = ?",
            ("x" * 32, user.id),
        )
        assert auth_manager.trust_device(user.id, "nofp_device") is True

        with patch.object(encryption, "verify_password", return_value=True):
            result = auth_manager.login("trust_nofp", "TestPass123!")
        assert result.status.value == "2fa_required"

    def test_untrust_device_restores_2fa(self, db, auth_manager):
        """Untrusting a device returns it to challenge-required behavior."""
        from src.utils import encryption

        user = self._register_and_login_with_device(
            auth_manager, "trust_untrust", "untrust_fp"
        )
        self._mgr(auth_manager)._db.execute(
            "UPDATE auth_users SET totp_enabled = 1, totp_secret_encrypted = ? WHERE id = ?",
            ("x" * 32, user.id),
        )

        assert auth_manager.trust_device(user.id, "untrust_fp") is True
        assert auth_manager.is_trusted_device(user.id, "untrust_fp") is True
        assert auth_manager.untrust_device(user.id, "untrust_fp") is True
        assert auth_manager.is_trusted_device(user.id, "untrust_fp") is False

        with patch.object(encryption, "verify_password", return_value=True):
            result = auth_manager.login(
                "trust_untrust",
                "TestPass123!",
                device_info={"fingerprint": "untrust_fp"},
            )
        assert result.status.value == "2fa_required"

    def test_trust_is_per_user_scoped(self, db, auth_manager):
        """User B must not inherit trust from user A's device fingerprint."""
        user_a = self._register_and_login_with_device(
            auth_manager, "trust_owner", "shared_fp"
        )
        user_b = self._register_and_login_with_device(
            auth_manager, "trust_other", "other_fp"
        )

        assert auth_manager.trust_device(user_a.id, "shared_fp") is True
        assert auth_manager.is_trusted_device(user_a.id, "shared_fp") is True
        assert auth_manager.is_trusted_device(user_b.id, "shared_fp") is False
