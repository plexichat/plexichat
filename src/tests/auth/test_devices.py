"""
Comprehensive device tests covering tracking, management, and security.
"""

import pytest
import time
from src.tests.fixtures.config import TEST_PASSWORD


class TestDeviceTracking:
    """Tests for device tracking."""

    def test_device_tracked_on_login(self, modules):
        """Test device is tracked on login."""
        username = f"devtrack_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        device_info = {
            "fingerprint": "device123",
            "name": "Chrome Browser",
            "type": "web",
        }

        modules.auth.login(username, TEST_PASSWORD, device_info=device_info)
        devices = modules.auth.get_devices(user.id)

        assert len(devices) > 0
        assert any(d.fingerprint == "device123" for d in devices)

    def test_device_first_seen_timestamp(self, modules):
        """Test device has first_seen timestamp."""
        username = f"firstseen_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        device_info = {"fingerprint": "device456", "name": "Firefox"}
        modules.auth.login(username, TEST_PASSWORD, device_info=device_info)

        devices = modules.auth.get_devices(user.id)
        device = [d for d in devices if d.fingerprint == "device456"][0]

        assert device.first_seen_at > 0

    def test_device_last_seen_updates(self, modules):
        """Test device last_seen updates on subsequent logins."""
        username = f"lastseen_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        device_info = {"fingerprint": "device789", "name": "Safari"}

        modules.auth.login(username, TEST_PASSWORD, device_info=device_info)
        devices1 = modules.auth.get_devices(user.id)
        device1 = [d for d in devices1 if d.fingerprint == "device789"][0]
        first_last_seen = device1.last_seen_at

        time.sleep(0.1)
        modules.auth.login(username, TEST_PASSWORD, device_info=device_info)

        devices2 = modules.auth.get_devices(user.id)
        device2 = [d for d in devices2 if d.fingerprint == "device789"][0]

        assert device2.last_seen_at >= first_last_seen

    def test_same_device_no_duplicate(self, modules):
        """Test same device doesn't create duplicate entries."""
        username = f"nodupe_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        device_info = {"fingerprint": "device111", "name": "Edge"}

        modules.auth.login(username, TEST_PASSWORD, device_info=device_info)
        modules.auth.login(username, TEST_PASSWORD, device_info=device_info)
        modules.auth.login(username, TEST_PASSWORD, device_info=device_info)

        devices = modules.auth.get_devices(user.id)
        matching = [d for d in devices if d.fingerprint == "device111"]

        assert len(matching) == 1

    def test_different_devices_tracked_separately(self, modules):
        """Test different devices are tracked separately."""
        username = f"multdev_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        devices_info = [
            {"fingerprint": "chrome1", "name": "Chrome Desktop", "type": "desktop"},
            {"fingerprint": "firefox1", "name": "Firefox Desktop", "type": "desktop"},
            {"fingerprint": "mobile1", "name": "Mobile App", "type": "mobile"},
        ]

        for dev_info in devices_info:
            modules.auth.login(username, TEST_PASSWORD, device_info=dev_info)

        devices = modules.auth.get_devices(user.id)
        assert len(devices) >= 3

        fingerprints = {d.fingerprint for d in devices}
        assert "chrome1" in fingerprints
        assert "firefox1" in fingerprints
        assert "mobile1" in fingerprints


class TestDeviceManagement:
    """Tests for device management operations."""

    def test_get_devices_empty(self, modules):
        """Test getting devices for user with no devices."""
        username = f"nodev_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        devices = modules.auth.get_devices(user.id)
        assert devices == []

    def test_get_devices_ordered_by_last_seen(self, modules):
        """Test devices are ordered by last seen."""
        username = f"ordered_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Create devices with delays
        for i in range(3):
            device_info = {"fingerprint": f"dev{i}", "name": f"Device {i}"}
            modules.auth.login(username, TEST_PASSWORD, device_info=device_info)
            time.sleep(0.1)

        devices = modules.auth.get_devices(user.id)

        # Should be in descending order
        for i in range(len(devices) - 1):
            assert devices[i].last_seen_at >= devices[i + 1].last_seen_at

    def test_rename_device(self, modules):
        """Test renaming a device."""
        username = f"rename_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        device_info = {"fingerprint": "renamedev", "name": "Old Name"}
        modules.auth.login(username, TEST_PASSWORD, device_info=device_info)

        devices = modules.auth.get_devices(user.id)
        device = [d for d in devices if d.fingerprint == "renamedev"][0]

        result = modules.auth.rename_device(user.id, device.id, "New Name")
        assert result is True

        devices = modules.auth.get_devices(user.id)
        device = [d for d in devices if d.fingerprint == "renamedev"][0]
        assert device.name == "New Name"

    def test_rename_device_wrong_user(self, modules):
        """Test cannot rename device owned by another user."""
        user1 = f"user1_{time.time()}"
        user2 = f"user2_{time.time()}"

        u1 = modules.auth.register(user1, f"{user1}@test.com", TEST_PASSWORD)
        u2 = modules.auth.register(user2, f"{user2}@test.com", TEST_PASSWORD)

        device_info = {"fingerprint": "dev1", "name": "Device"}
        modules.auth.login(user1, TEST_PASSWORD, device_info=device_info)

        devices = modules.auth.get_devices(u1.id)
        device_id = devices[0].id

        result = modules.auth.rename_device(u2.id, device_id, "Hacked Name")
        assert result is False

    def test_revoke_device(self, modules):
        """Test revoking a device."""
        username = f"revoke_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        device_info = {"fingerprint": "revokedev", "name": "Device"}
        modules.auth.login(username, TEST_PASSWORD, device_info=device_info)

        devices = modules.auth.get_devices(user.id)
        device_id = devices[0].id

        result = modules.auth.revoke_device(user.id, device_id)
        assert result is True

        devices = modules.auth.get_devices(user.id)
        device_ids = [d.id for d in devices]
        assert device_id not in device_ids

    def test_revoke_device_revokes_sessions(self, modules):
        """Test revoking device also revokes its sessions."""
        username = f"revokesess_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        device_info = {"fingerprint": "sessdev", "name": "Device"}
        result = modules.auth.login(username, TEST_PASSWORD, device_info=device_info)
        token = result.token

        devices = modules.auth.get_devices(user.id)
        device_id = devices[0].id

        modules.auth.revoke_device(user.id, device_id)

        # Token should be invalid
        from src.core.auth.exceptions import TokenInvalidError

        with pytest.raises(TokenInvalidError):
            modules.auth.verify_token(token)

    def test_revoke_device_wrong_user(self, modules):
        """Test cannot revoke device owned by another user."""
        user1 = f"user1_{time.time()}"
        user2 = f"user2_{time.time()}"

        u1 = modules.auth.register(user1, f"{user1}@test.com", TEST_PASSWORD)
        u2 = modules.auth.register(user2, f"{user2}@test.com", TEST_PASSWORD)

        device_info = {"fingerprint": "dev2", "name": "Device"}
        modules.auth.login(user1, TEST_PASSWORD, device_info=device_info)

        devices = modules.auth.get_devices(u1.id)
        device_id = devices[0].id

        result = modules.auth.revoke_device(u2.id, device_id)
        assert result is False


class TestDeviceFingerprinting:
    """Tests for device fingerprinting."""

    def test_device_requires_fingerprint(self, modules):
        """Test device tracking requires fingerprint."""
        username = f"nofinger_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Empty fingerprint
        device_info = {"fingerprint": "", "name": "Device"}
        modules.auth.login(username, TEST_PASSWORD, device_info=device_info)

        # Should not create device without fingerprint
        devices = modules.auth.get_devices(user.id)
        assert len(devices) == 0

    def test_device_fingerprint_uniqueness(self, modules):
        """Test device fingerprint identifies unique devices."""
        username = f"unique_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Same fingerprint, different name
        device1 = {"fingerprint": "abc123", "name": "Chrome"}
        device2 = {"fingerprint": "abc123", "name": "Firefox"}

        modules.auth.login(username, TEST_PASSWORD, device_info=device1)
        modules.auth.login(username, TEST_PASSWORD, device_info=device2)

        devices = modules.auth.get_devices(user.id)
        matching = [d for d in devices if d.fingerprint == "abc123"]

        # Should be one device (fingerprint is the key)
        assert len(matching) == 1

    def test_device_type_recorded(self, modules):
        """Test device type is recorded."""
        username = f"devtype_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        device_info = {"fingerprint": "type123", "name": "iPhone", "type": "mobile"}

        modules.auth.login(username, TEST_PASSWORD, device_info=device_info)

        devices = modules.auth.get_devices(user.id)
        device = [d for d in devices if d.fingerprint == "type123"][0]

        assert device.device_type == "mobile"


class TestDeviceAudit:
    """Tests for device audit logging."""

    def test_device_revocation_audited(self, modules):
        """Test device revocation creates audit log."""
        username = f"auditrev_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        device_info = {"fingerprint": "auditdev", "name": "Device"}
        modules.auth.login(username, TEST_PASSWORD, device_info=device_info)

        devices = modules.auth.get_devices(user.id)
        device_id = devices[0].id

        modules.auth.revoke_device(user.id, device_id)

        events = modules.auth.get_security_events(user.id, limit=10)
        revoke_events = [e for e in events if e.event_type.value == "device_revoked"]
        assert len(revoke_events) > 0


class TestDeviceEdgeCases:
    """Edge case tests for devices."""

    def test_device_without_name(self, modules):
        """Test device tracking works without name."""
        username = f"noname_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        device_info = {"fingerprint": "noname123"}
        modules.auth.login(username, TEST_PASSWORD, device_info=device_info)

        devices = modules.auth.get_devices(user.id)
        assert len(devices) > 0

    def test_device_without_type(self, modules):
        """Test device tracking works without type."""
        username = f"notype_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        device_info = {"fingerprint": "notype123", "name": "Device"}
        modules.auth.login(username, TEST_PASSWORD, device_info=device_info)

        devices = modules.auth.get_devices(user.id)
        assert len(devices) > 0

    def test_device_long_fingerprint(self, modules):
        """Test device with long fingerprint."""
        username = f"longfinger_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        long_fingerprint = "a" * 500
        device_info = {"fingerprint": long_fingerprint, "name": "Device"}
        modules.auth.login(username, TEST_PASSWORD, device_info=device_info)

        devices = modules.auth.get_devices(user.id)
        device = devices[0]
        assert device.fingerprint == long_fingerprint

    def test_device_special_chars_in_name(self, modules):
        """Test device name with special characters."""
        username = f"specialname_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        device_info = {
            "fingerprint": "special123",
            "name": "Device <script>alert('xss')</script>",
        }
        modules.auth.login(username, TEST_PASSWORD, device_info=device_info)

        devices = modules.auth.get_devices(user.id)
        device = [d for d in devices if d.fingerprint == "special123"][0]
        assert device.name == "Device <script>alert('xss')</script>"


class TestDeviceSessionRelationship:
    """Tests for device-session relationship."""

    def test_session_linked_to_device(self, modules):
        """Test session is linked to device."""
        username = f"linkdev_{time.time()}"
        modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        device_info = {"fingerprint": "link123", "name": "Device"}
        result = modules.auth.login(username, TEST_PASSWORD, device_info=device_info)

        assert result.session.device_id is not None

    def test_multiple_sessions_same_device(self, modules):
        """Test multiple sessions can use same device."""
        username = f"multisess_{time.time()}"
        modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        device_info = {"fingerprint": "shared123", "name": "Device"}

        result1 = modules.auth.login(username, TEST_PASSWORD, device_info=device_info)
        result2 = modules.auth.login(username, TEST_PASSWORD, device_info=device_info)

        assert result1.session.device_id == result2.session.device_id


class TestDeviceSecurity:
    """Tests for device security features."""

    def test_device_isolation_between_users(self, modules):
        """Test devices are isolated between users."""
        user1 = f"user1_{time.time()}"
        user2 = f"user2_{time.time()}"

        u1 = modules.auth.register(user1, f"{user1}@test.com", TEST_PASSWORD)
        u2 = modules.auth.register(user2, f"{user2}@test.com", TEST_PASSWORD)

        device_info = {"fingerprint": "shared_finger", "name": "Device"}

        modules.auth.login(user1, TEST_PASSWORD, device_info=device_info)
        modules.auth.login(user2, TEST_PASSWORD, device_info=device_info)

        devices1 = modules.auth.get_devices(u1.id)
        devices2 = modules.auth.get_devices(u2.id)

        # Each user should have their own device record
        assert len(devices1) >= 1
        assert len(devices2) >= 1

        # Device IDs should be different
        dev1_ids = {d.id for d in devices1}
        dev2_ids = {d.id for d in devices2}
        assert dev1_ids.isdisjoint(dev2_ids)


class TestDeviceListPerformance:
    """Tests for device listing performance."""

    def test_get_devices_with_many_devices(self, modules):
        """Test getting devices when user has many devices."""
        username = f"manydev_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Create 20 devices
        for i in range(20):
            device_info = {
                "fingerprint": f"dev{i}_{time.time()}",
                "name": f"Device {i}",
            }
            modules.auth.login(username, TEST_PASSWORD, device_info=device_info)

        devices = modules.auth.get_devices(user.id)
        assert len(devices) >= 20
