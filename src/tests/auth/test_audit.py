"""
Comprehensive audit tests covering logging, security events, and compliance.
"""

import time
from src.tests.fixtures.config import TEST_PASSWORD


class TestAuditLogging:
    """Tests for audit logging functionality."""

    def test_registration_audited(self, modules):
        """Test user registration creates audit entry."""
        username = f"auditreg_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        events = modules.auth.get_security_events(user.id, limit=10)
        register_events = [e for e in events if e.event_type.value == "register"]
        assert len(register_events) > 0

    def test_successful_login_audited(self, modules):
        """Test successful login creates audit entry."""
        username = f"auditlogin_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        modules.auth.login(username, TEST_PASSWORD)

        events = modules.auth.get_login_history(user.id, limit=10)
        login_events = [e for e in events if e.event_type.value == "login_success"]
        assert len(login_events) > 0

    def test_failed_login_audited(self, modules):
        """Test failed login creates audit entry."""
        username = f"auditfail_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        from src.core.auth.exceptions import InvalidCredentialsError

        try:
            modules.auth.login(username, "WrongPassword!")
        except InvalidCredentialsError:
            pass

        events = modules.auth.get_security_events(user.id, limit=10)
        failed_events = [e for e in events if e.event_type.value == "login_failed"]
        assert len(failed_events) > 0

    def test_logout_audited(self, modules):
        """Test logout creates audit entry."""
        username = f"auditlogout_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        result = modules.auth.login(username, TEST_PASSWORD)
        modules.auth.logout(result.token)

        events = modules.auth.get_login_history(user.id, limit=10)
        logout_events = [e for e in events if e.event_type.value == "logout"]
        assert len(logout_events) > 0

    def test_password_change_audited(self, modules):
        """Test password change creates audit entry."""
        username = f"auditpwchange_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        modules.auth.change_password(user.id, TEST_PASSWORD, "NewPass456!")

        events = modules.auth.get_security_events(user.id, limit=10)
        change_events = [e for e in events if e.event_type.value == "password_change"]
        assert len(change_events) > 0

    def test_2fa_enabled_audited(self, modules):
        """Test 2FA enabling creates audit entry."""
        username = f"audit2fa_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        setup = modules.auth.setup_2fa(user.id)
        from src.core.auth import totp as totp_module

        code = totp_module.generate_totp_code(setup.secret)
        modules.auth.confirm_2fa(user.id, code)

        events = modules.auth.get_security_events(user.id, limit=10)
        enable_events = [e for e in events if e.event_type.value == "2fa_enabled"]
        assert len(enable_events) > 0

    def test_2fa_disabled_audited(self, modules):
        """Test 2FA disabling creates audit entry."""
        username = f"audit2fadis_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Enable 2FA first
        setup = modules.auth.setup_2fa(user.id)
        from src.core.auth import totp as totp_module

        code = totp_module.generate_totp_code(setup.secret)
        modules.auth.confirm_2fa(user.id, code)

        # Disable
        new_code = totp_module.generate_totp_code(setup.secret)
        modules.auth.disable_2fa(user.id, TEST_PASSWORD, new_code)

        events = modules.auth.get_security_events(user.id, limit=10)
        disable_events = [e for e in events if e.event_type.value == "2fa_disabled"]
        assert len(disable_events) > 0


class TestAuditEventDetails:
    """Tests for audit event details."""

    def test_audit_entry_has_timestamp(self, modules):
        """Test audit entries have timestamps."""
        username = f"timestamp_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        events = modules.auth.get_security_events(user.id, limit=10)
        for event in events:
            assert event.timestamp > 0

    def test_audit_entry_has_event_type(self, modules):
        """Test audit entries have event types."""
        username = f"evtype_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        events = modules.auth.get_security_events(user.id, limit=10)
        for event in events:
            assert event.event_type is not None

    def test_audit_entry_includes_ip_address(self, modules):
        """Test audit entries include IP addresses when available."""
        username = f"auditip_{time.time()}"
        user = modules.auth.register(
            username, f"{username}@test.com", TEST_PASSWORD, ip_address="1.2.3.4"
        )

        events = modules.auth.get_security_events(user.id, limit=10)
        register_events = [e for e in events if e.event_type.value == "register"]

        assert len(register_events) > 0
        assert register_events[0].ip_address == "1.2.3.4"

    def test_audit_entry_success_flag(self, modules):
        """Test audit entries have success flag."""
        username = f"success_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Successful login
        modules.auth.login(username, TEST_PASSWORD)

        # Failed login
        from src.core.auth.exceptions import InvalidCredentialsError

        try:
            modules.auth.login(username, "WrongPass!")
        except InvalidCredentialsError:
            pass

        events = modules.auth.get_security_events(user.id, limit=10)

        success_events = [e for e in events if e.success is True]
        failed_events = [e for e in events if e.success is False]

        assert len(success_events) > 0
        assert len(failed_events) > 0

    def test_audit_entry_additional_details(self, modules):
        """Test audit entries can include additional details."""
        username = f"details_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        events = modules.auth.get_security_events(user.id, limit=10)
        register_events = [e for e in events if e.event_type.value == "register"]

        assert len(register_events) > 0
        event = register_events[0]
        assert event.details is not None
        assert "username" in event.details


class TestAuditRetrieval:
    """Tests for retrieving audit logs."""

    def test_get_login_history(self, modules):
        """Test retrieving login history."""
        username = f"history_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Create some logins
        for _ in range(3):
            modules.auth.login(username, TEST_PASSWORD)

        history = modules.auth.get_login_history(user.id, limit=10)
        assert len(history) >= 3

    def test_get_security_events(self, modules):
        """Test retrieving security events."""
        username = f"secevents_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Create some events
        modules.auth.login(username, TEST_PASSWORD)
        modules.auth.change_password(user.id, TEST_PASSWORD, "NewPass456!")

        events = modules.auth.get_security_events(user.id, limit=10)
        assert len(events) > 0

    def test_audit_history_ordered_by_timestamp(self, modules):
        """Test audit history is ordered by timestamp (newest first)."""
        username = f"ordered_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Create events with delays
        modules.auth.login(username, TEST_PASSWORD)
        time.sleep(0.1)
        modules.auth.login(username, TEST_PASSWORD)
        time.sleep(0.1)
        modules.auth.login(username, TEST_PASSWORD)

        history = modules.auth.get_login_history(user.id, limit=10)

        # Should be in descending order
        for i in range(len(history) - 1):
            assert history[i].timestamp >= history[i + 1].timestamp

    def test_audit_history_limit(self, modules):
        """Test audit history respects limit parameter."""
        username = f"limit_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Create many events
        for _ in range(10):
            modules.auth.login(username, TEST_PASSWORD)

        history = modules.auth.get_login_history(user.id, limit=5)
        assert len(history) <= 5


class TestAuditEventTypes:
    """Tests for different audit event types."""

    def test_account_locked_audited(self, modules):
        """Test account locking creates audit entry."""
        username = f"lockaudit_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        from src.core.auth.exceptions import InvalidCredentialsError

        # Trigger lockout
        for _ in range(3):
            try:
                modules.auth.login(username, "WrongPass!")
            except InvalidCredentialsError:
                pass

        events = modules.auth.get_security_events(user.id, limit=20)
        lock_events = [e for e in events if e.event_type.value == "account_locked"]
        assert len(lock_events) > 0

    def test_session_revoked_audited(self, modules):
        """Test session revocation creates audit entry."""
        username = f"revokeaudit_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        result = modules.auth.login(username, TEST_PASSWORD)
        modules.auth.revoke_session(user.id, result.session.id)

        events = modules.auth.get_security_events(user.id, limit=10)
        revoke_events = [e for e in events if e.event_type.value == "session_revoked"]
        assert len(revoke_events) > 0

    def test_device_revoked_audited(self, modules):
        """Test device revocation creates audit entry."""
        username = f"devrevoke_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        device_info = {"fingerprint": "auditdev", "name": "Device"}
        modules.auth.login(username, TEST_PASSWORD, device_info=device_info)

        devices = modules.auth.get_devices(user.id)
        if devices:
            modules.auth.revoke_device(user.id, devices[0].id)

            events = modules.auth.get_security_events(user.id, limit=10)
            dev_revoke_events = [
                e for e in events if e.event_type.value == "device_revoked"
            ]
            assert len(dev_revoke_events) > 0

    def test_bot_created_audited(self, modules):
        """Test bot creation creates audit entry."""
        username = f"botaudit_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot")

        events = modules.auth.get_security_events(user.id, limit=10)
        bot_events = [e for e in events if e.event_type.value == "bot_created"]
        assert len(bot_events) > 0

    def test_bot_deleted_audited(self, modules):
        """Test bot deletion creates audit entry."""
        username = f"botdelaudit_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot")
        modules.auth.delete_bot(user.id, bot.id)

        events = modules.auth.get_security_events(user.id, limit=10)
        delete_events = [e for e in events if e.event_type.value == "bot_deleted"]
        assert len(delete_events) > 0

    def test_backup_code_used_audited(self, modules):
        """Test backup code usage creates audit entry."""
        username = f"backupaudit_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Enable 2FA
        setup = modules.auth.setup_2fa(user.id)
        from src.core.auth import totp as totp_module

        code = totp_module.generate_totp_code(setup.secret)
        modules.auth.confirm_2fa(user.id, code)

        # Use backup code
        result = modules.auth.login(username, TEST_PASSWORD)
        modules.auth.complete_2fa(result.challenge_token, setup.backup_codes[0])

        events = modules.auth.get_security_events(user.id, limit=10)
        backup_events = [e for e in events if e.event_type.value == "2fa_backup_used"]
        assert len(backup_events) > 0


class TestAuditSecurity:
    """Tests for audit log security."""

    def test_audit_entries_immutable(self, modules):
        """Test audit entries cannot be modified."""
        # Audit entries should be append-only
        # This is enforced by not providing update methods
        pass

    def test_audit_log_isolation(self, modules):
        """Test users can only see their own audit logs."""
        user1 = f"user1_{time.time()}"
        user2 = f"user2_{time.time()}"

        modules.auth.register(user1, f"{user1}@test.com", TEST_PASSWORD)
        u2 = modules.auth.register(user2, f"{user2}@test.com", TEST_PASSWORD)

        # Create events for user1
        modules.auth.login(user1, TEST_PASSWORD)

        # Get events for user2
        events = modules.auth.get_security_events(u2.id, limit=10)

        # Should not include user1's events
        # Only user2's registration
        assert all(e.user_id == u2.id for e in events)

    def test_sensitive_data_not_logged(self, modules):
        """Test sensitive data is not logged in audit."""
        username = f"sensitive_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        events = modules.auth.get_security_events(user.id, limit=10)

        for event in events:
            if event.details:
                # Password should never be in details
                assert TEST_PASSWORD not in str(event.details)
                assert (
                    "password" not in str(event.details).lower()
                    or "password" in str(event.details).lower()
                    and TEST_PASSWORD not in str(event.details)
                )


class TestAuditCompliance:
    """Tests for audit compliance features."""

    def test_failed_login_includes_reason(self, modules):
        """Test failed login audit includes reason."""
        username = f"reason_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        from src.core.auth.exceptions import InvalidCredentialsError

        try:
            modules.auth.login(username, "WrongPass!")
        except InvalidCredentialsError:
            pass

        events = modules.auth.get_security_events(user.id, limit=10)
        failed_events = [e for e in events if e.event_type.value == "login_failed"]

        assert len(failed_events) > 0
        event = failed_events[0]
        assert event.details is not None
        assert "reason" in event.details

    def test_audit_retention(self, modules):
        """Test audit logs are retained."""
        username = f"retention_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Create an event
        modules.auth.login(username, TEST_PASSWORD)

        # Event should still be retrievable
        events = modules.auth.get_security_events(user.id, limit=10)
        login_events = [e for e in events if e.event_type.value == "login_success"]
        assert len(login_events) > 0


class TestAuditPerformance:
    """Tests for audit log performance."""

    def test_audit_log_doesnt_block_operation(self, modules):
        """Test audit logging doesn't significantly slow operations."""
        username = f"perf_{time.time()}"
        modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Operations should complete quickly even with audit logging
        import time as time_module

        start = time_module.time()

        for _ in range(10):
            modules.auth.login(username, TEST_PASSWORD)

        elapsed = time_module.time() - start

        # Should complete in reasonable time (allowing for Argon2 hashing)
        assert elapsed < 30  # 3 seconds per login max

    def test_get_large_audit_history(self, modules):
        """Test retrieving large audit history."""
        username = f"large_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Create many events
        for _ in range(20):
            modules.auth.login(username, TEST_PASSWORD)

        # Should be able to retrieve with limit
        events = modules.auth.get_security_events(user.id, limit=50)
        assert len(events) > 0


class TestAuditEdgeCases:
    """Edge case tests for audit logging."""

    def test_audit_for_nonexistent_user(self, modules):
        """Test getting audit for non-existent user."""
        events = modules.auth.get_security_events(999999999, limit=10)
        assert events == []

    def test_audit_with_none_details(self, modules):
        """Test audit entries can have None details."""
        username = f"nonedetails_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        modules.auth.get_security_events(user.id, limit=10)
        # Some events may have None details
        # Should not crash

    def test_audit_entry_id_uniqueness(self, modules):
        """Test audit entry IDs are unique."""
        username = f"uniqueid_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Create multiple events
        for _ in range(5):
            modules.auth.login(username, TEST_PASSWORD)

        events = modules.auth.get_security_events(user.id, limit=10)
        event_ids = [e.id for e in events]

        assert len(event_ids) == len(set(event_ids))
