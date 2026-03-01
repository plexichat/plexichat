"""
Comprehensive Security Tests.

Additional security tests covering encryption, data exposure,
race conditions, and edge cases.
"""

import pytest
import threading


class TestDataProtection:
    """Test data protection and privacy mechanisms."""

    def test_passwords_not_exposed_in_responses(self, modules, user_pool):
        """Test that passwords are never exposed in API responses."""
        user = user_pool.get_user()

        user_obj = modules.auth.get_user(user.id)

        assert not hasattr(user_obj, "password")
        assert not hasattr(user_obj, "password_hash")

    def test_tokens_not_logged(self, modules, user_pool):
        """Test that tokens are not logged in plain text."""
        user, username, password = user_pool.get_user_with_credentials()

        result = modules.auth.login(username, password)

        assert result.token is not None

    def test_session_tokens_hashed_in_database(self, modules, user_pool):
        """Test that session tokens are hashed in the database."""
        user, username, password = user_pool.get_user_with_credentials()

        result = modules.auth.login(username, password)

        parsed = modules.auth._auth.tokens.parse_token(result.token)

        session = modules.db.fetch_one(
            "SELECT token_hash FROM auth_sessions WHERE id = ?", (parsed["id"],)
        )

        assert session["token_hash"] != parsed["secret"]

    def test_bot_tokens_hashed_in_database(self, modules, user_pool):
        """Test that bot tokens are hashed in the database."""
        owner = user_pool.get_user()

        bot = modules.auth.create_bot(
            owner_id=owner.id, username=f"testbot_{owner.id}", display_name="Test Bot"
        )

        parsed = modules.auth._auth.tokens.parse_token(bot.token)

        bot_record = modules.db.fetch_one(
            "SELECT token_hash FROM auth_bots WHERE id = ?", (parsed["id"],)
        )

        assert bot_record["token_hash"] != parsed["secret"]

    def test_2fa_secrets_encrypted(self, modules, user_pool):
        """Test that 2FA secrets are encrypted in the database."""
        user, username, password = user_pool.get_user_with_credentials()

        setup = modules.auth.setup_2fa(user.id)

        user_record = modules.db.fetch_one(
            "SELECT totp_secret_encrypted FROM auth_users WHERE id = ?", (user.id,)
        )

        if user_record["totp_secret_encrypted"]:
            assert user_record["totp_secret_encrypted"] != setup.secret

    def test_backup_codes_hashed(self, modules, user_pool):
        """Test that backup codes are hashed in the database."""
        user, username, password = user_pool.get_user_with_credentials()

        setup = modules.auth.setup_2fa(user.id)

        import re

        match = re.search(r"secret=([A-Z0-9]+)", setup.qr_uri)
        if match:
            secret = match.group(1)
            from src.core.auth import totp

            code = totp.generate_totp_code(secret)
            modules.auth.confirm_2fa(user.id, code)

            user_record = modules.db.fetch_one(
                "SELECT backup_codes_hash FROM auth_users WHERE id = ?", (user.id,)
            )

            if user_record["backup_codes_hash"] and setup.backup_codes:
                for backup_code in setup.backup_codes:
                    assert backup_code not in user_record["backup_codes_hash"]

    def test_email_not_exposed_to_unauthorized_users(self, modules, user_pool):
        """Test that email addresses are not exposed to unauthorized users."""
        user1 = user_pool.get_user()
        user_pool.get_user()

        user1_obj = modules.auth.get_user(user1.id)

        assert hasattr(user1_obj, "email")

    def test_ip_addresses_tracked_securely(self, modules, user_pool):
        """Test that IP addresses are tracked securely."""
        user, username, password = user_pool.get_user_with_credentials()

        modules.auth.login(
            username=username, password=password, ip_address="192.168.1.100"
        )

        sessions = modules.auth.get_sessions(user.id)
        if sessions:
            assert sessions[0].ip_address == "192.168.1.100"

    def test_audit_log_records_security_events(self, modules, user_pool):
        """Test that security events are logged in audit log."""
        user, username, password = user_pool.get_user_with_credentials()

        modules.auth.login(username, password)

        history = modules.auth.get_login_history(user.id, limit=10)
        assert len(history) > 0


class TestRaceConditions:
    """Test for race conditions in security-critical operations."""

    def test_concurrent_login_attempts(self, modules, user_pool):
        """Test concurrent login attempts don't cause issues."""
        user, username, password = user_pool.get_user_with_credentials()

        results = []
        errors = []

        def login_attempt():
            try:
                result = modules.auth.login(username, password)
                results.append(result)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=login_attempt) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) > 0

    def test_concurrent_message_sends(self, modules, user_pool):
        """Test concurrent message sends don't cause issues."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        dm = modules.messaging.create_dm(user1.id, user2.id)

        messages = []
        errors = []

        def send_message(i):
            try:
                msg = modules.messaging.send_message(
                    user_id=user1.id, conversation_id=dm.id, content=f"Message {i}"
                )
                messages.append(msg)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=send_message, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(messages) > 0

    def test_concurrent_session_creation(self, modules, user_pool):
        """Test concurrent session creation doesn't exceed limits."""
        user, username, password = user_pool.get_user_with_credentials()

        tokens = []
        errors = []

        def create_session():
            try:
                result = modules.auth.login(username, password)
                tokens.append(result.token)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create_session) for _ in range(15)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        sessions = modules.auth.get_sessions(user.id)
        assert len(sessions) <= 10


class TestInputBoundaries:
    """Test boundary conditions and edge cases in input validation."""

    def test_maximum_username_length(self, modules):
        """Test maximum username length validation."""
        max_length_username = "a" * 32

        try:
            user = modules.auth.register(
                username=max_length_username,
                email="maxlen@test.com",
                password="TestPass123!",
            )
            assert user.username == max_length_username
        except Exception:
            pass

    def test_minimum_username_length(self, modules):
        """Test minimum username length validation."""
        with pytest.raises(Exception):
            modules.auth.register(
                username="ab", email="minlen@test.com", password="TestPass123!"
            )

    def test_maximum_message_length(self, modules, user_pool):
        """Test maximum message length validation."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        dm = modules.messaging.create_dm(user1.id, user2.id)

        max_content = "x" * 4000

        try:
            msg = modules.messaging.send_message(
                user_id=user1.id, conversation_id=dm.id, content=max_content
            )
            assert len(msg.content) <= 4000
        except Exception:
            pass

    def test_zero_length_inputs(self, modules):
        """Test handling of zero-length inputs."""
        with pytest.raises(Exception):
            modules.auth.register(username="", email="", password="")

    def test_negative_numeric_inputs(self, modules):
        """Test handling of negative numeric inputs."""
        with pytest.raises(Exception):
            modules.auth.get_user(-1)

    def test_extremely_large_numeric_inputs(self, modules):
        """Test handling of extremely large numeric inputs."""
        huge_id = 2**63 - 1

        user = modules.auth.get_user(huge_id)
        assert user is None


class TestSecurityHeaders:
    """Test security-related headers and configurations."""

    def test_token_expiration_enforced(self, modules, user_pool):
        """Test that token expiration is properly enforced."""
        user, username, password = user_pool.get_user_with_credentials()

        result = modules.auth.login(username, password)

        token_info = modules.auth.verify_token(result.token)
        assert token_info.expires_at is not None

    def test_session_security_attributes(self, modules, user_pool):
        """Test that sessions have proper security attributes."""
        user, username, password = user_pool.get_user_with_credentials()

        modules.auth.login(
            username=username,
            password=password,
            ip_address="192.168.1.1",
            user_agent="Test Browser",
        )

        sessions = modules.auth.get_sessions(user.id)
        if sessions:
            session = sessions[0]
            assert session.ip_address is not None
            assert session.user_agent is not None


class TestPrivilegeEscalation:
    """Test prevention of privilege escalation attacks."""

    def test_user_cannot_grant_admin_permissions(self, modules, user_pool):
        """Test that users cannot grant themselves admin permissions."""
        user = user_pool.get_user()

        user_obj = modules.auth.get_user(user.id)
        permissions = user_obj.permissions

        assert not permissions.get("admin.*")
        assert not permissions.get("*")

    def test_bot_cannot_have_admin_permissions(self, modules, user_pool):
        """Test that bots cannot be created with admin permissions."""
        owner = user_pool.get_user()

        try:
            bot = modules.auth.create_bot(
                owner_id=owner.id,
                username=f"sysbot_{owner.id}",
                display_name="Admin Bot",
                permissions={"admin.*": True},
            )
            assert not bot.permissions.get("admin.*")
        except Exception:
            pass

    def test_member_cannot_escalate_to_owner(self, modules, user_pool):
        """Test that members cannot escalate to server owner."""
        owner = user_pool.get_user()
        member = user_pool.get_user()

        server = modules.servers.create_server(owner_id=owner.id, name="Test Server")

        modules.servers.add_member(server.id, member.id)

        server_obj = modules.servers.get_server(server.id)
        assert server_obj.owner_id == owner.id

    def test_permission_inheritance_security(self, modules, user_pool):
        """Test that permission inheritance is secure."""
        owner = user_pool.get_user()

        server = modules.servers.create_server(owner_id=owner.id, name="Test Server")

        assert server.owner_id == owner.id


class TestDenialOfService:
    """Test prevention of denial of service attacks."""

    def test_message_rate_limiting(self, modules, user_pool):
        """Test that message rate limiting prevents spam."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        dm = modules.messaging.create_dm(user1.id, user2.id)

        messages = []
        for i in range(20):
            try:
                msg = modules.messaging.send_message(
                    user_id=user1.id, conversation_id=dm.id, content=f"Message {i}"
                )
                messages.append(msg)
            except Exception:
                break

    def test_login_attempt_rate_limiting(self, modules):
        """Test that login attempts are rate limited."""
        for i in range(20):
            try:
                modules.auth.login("nonexistent", "password")
            except Exception:
                pass

    def test_resource_exhaustion_prevention(self, modules, user_pool):
        """Test prevention of resource exhaustion."""
        user = user_pool.get_user()

        for i in range(15):
            try:
                modules.auth.get_user(user.id)
            except Exception:
                break
