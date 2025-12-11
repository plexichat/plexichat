"""
Device management tests for auth module.
"""

import pytest


class TestDevices:
    """Test device management."""

    def test_login_creates_device(self, registered_user):
        """Test login with device info creates device record."""
        user, auth, username = registered_user

        fp = f"device_{user.id}_create"
        auth.login(
            username,
            "TestPass123!",
            device_info={"fingerprint": fp, "name": "Test Device", "type": "desktop"}
        )

        devices = auth.get_devices(user.id)
        assert any(d.fingerprint == fp for d in devices)

    def test_same_fingerprint_reuses_device(self, registered_user):
        """Test same fingerprint reuses device record."""
        user, auth, username = registered_user

        fp = f"same_device_{user.id}"
        auth.login(username, "TestPass123!", device_info={"fingerprint": fp})
        auth.login(username, "TestPass123!", device_info={"fingerprint": fp})

        devices = auth.get_devices(user.id)
        same_fp = [d for d in devices if d.fingerprint == fp]
        assert len(same_fp) == 1

    def test_different_fingerprint_creates_new(self, registered_user):
        """Test different fingerprint creates new device."""
        user, auth, username = registered_user

        auth.login(username, "TestPass123!", device_info={"fingerprint": f"dev1_{user.id}"})
        auth.login(username, "TestPass123!", device_info={"fingerprint": f"dev2_{user.id}"})

        devices = auth.get_devices(user.id)
        assert len(devices) >= 2

    def test_get_devices(self, registered_user):
        """Test getting user devices."""
        user, auth, username = registered_user

        auth.login(username, "TestPass123!", device_info={"fingerprint": f"fp1_{user.id}"})
        auth.login(username, "TestPass123!", device_info={"fingerprint": f"fp2_{user.id}"})

        devices = auth.get_devices(user.id)
        assert len(devices) >= 2

    def test_rename_device(self, registered_user):
        """Test renaming a device."""
        user, auth, username = registered_user

        fp = f"rename_fp_{user.id}"
        auth.login(username, "TestPass123!", device_info={"fingerprint": fp, "name": "Old Name"})

        devices = auth.get_devices(user.id)
        device = next(d for d in devices if d.fingerprint == fp)

        result = auth.rename_device(user.id, device.id, "New Name")
        assert result is True

    def test_rename_device_wrong_user(self, db_and_auth):
        """Test renaming device of another user fails."""
        db, auth = db_and_auth

        user1 = auth.register("devuser1", "devuser1@example.com", "TestPass123!")
        user2 = auth.register("devuser2", "devuser2@example.com", "TestPass123!")

        auth.login("devuser1", "TestPass123!", device_info={"fingerprint": "user1_device"})

        devices = auth.get_devices(user1.id)
        device = devices[0]

        result = auth.rename_device(user2.id, device.id, "Hacked")
        assert result is False

    def test_revoke_device(self, registered_user):
        """Test revoking a device."""
        user, auth, username = registered_user

        fp = f"revoke_fp_{user.id}"
        auth.login(username, "TestPass123!", device_info={"fingerprint": fp})

        devices = auth.get_devices(user.id)
        device = next(d for d in devices if d.fingerprint == fp)

        result = auth.revoke_device(user.id, device.id)
        assert result is True

        devices_after = auth.get_devices(user.id)
        assert not any(d.fingerprint == fp for d in devices_after)

    def test_revoke_device_logs_out_sessions(self, db_and_auth):
        """Test revoking device logs out all its sessions."""
        db, auth = db_and_auth

        user = auth.register("revokedev", "revokedev@example.com", "TestPass123!")

        result = auth.login("revokedev", "TestPass123!", device_info={"fingerprint": "session_device"})
        token = result.token

        devices = auth.get_devices(user.id)
        device = next(d for d in devices if d.fingerprint == "session_device")

        auth.revoke_device(user.id, device.id)

        with pytest.raises(auth.TokenInvalidError):
            auth.verify_token(token)

    def test_device_type_recorded(self, registered_user):
        """Test device type is recorded."""
        user, auth, username = registered_user

        fp = f"type_fp_{user.id}"
        auth.login(username, "TestPass123!", device_info={"fingerprint": fp, "type": "mobile"})

        devices = auth.get_devices(user.id)
        device = next(d for d in devices if d.fingerprint == fp)

        assert device.device_type == "mobile"
