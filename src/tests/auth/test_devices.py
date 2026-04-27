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
