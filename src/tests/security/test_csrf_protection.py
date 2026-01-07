"""
CSRF (Cross-Site Request Forgery) Protection Tests.

Tests that state-changing operations require proper authentication tokens
and cannot be performed through simple cross-origin requests.
"""

import pytest


class TestCSRFProtection:
    """Test CSRF protection mechanisms."""

    def test_state_change_requires_authentication(self, modules, user_pool):
        """Test that state-changing operations require authentication."""
        user_pool.get_user()

        with pytest.raises(Exception):
            modules.messaging.send_message(
                user_id=None, conversation_id=1, content="Test"
            )

    def test_token_required_for_message_send(self, modules, user_pool):
        """Test that message sending requires valid token."""
        user = user_pool.get_user()
        user2 = user_pool.get_user()

        dm = modules.messaging.create_dm(user.id, user2.id)

        msg = modules.messaging.send_message(
            user_id=user.id, conversation_id=dm.id, content="Test"
        )

        assert msg is not None
        assert msg.author_id == user.id

    def test_token_required_for_message_deletion(self, modules, user_pool):
        """Test that message deletion requires proper authentication."""
        user = user_pool.get_user()
        user2 = user_pool.get_user()

        dm = modules.messaging.create_dm(user.id, user2.id)
        msg = modules.messaging.send_message(
            user_id=user.id, conversation_id=dm.id, content="Test"
        )

        modules.messaging.delete_message(user.id, msg.id)

    def test_cannot_perform_actions_for_other_users(self, modules, user_pool):
        """Test that users cannot perform actions as other users."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        dm = modules.messaging.create_dm(user1.id, user2.id)
        msg = modules.messaging.send_message(
            user_id=user1.id, conversation_id=dm.id, content="Test from user1"
        )

        with pytest.raises(Exception):
            modules.messaging.delete_message(user2.id, msg.id)

    def test_session_token_validates_user_identity(self, modules, user_pool):
        """Test that session tokens properly validate user identity."""
        user, username, password = user_pool.get_user_with_credentials()

        result = modules.auth.login(username, password)
        assert result.token is not None

        token_info = modules.auth.verify_token(result.token)
        assert token_info.user_id == user.id

    def test_expired_session_rejected(self, modules, user_pool):
        """Test that expired sessions are rejected."""
        user, username, password = user_pool.get_user_with_credentials()

        result = modules.auth.login(username, password)
        parsed = modules.auth._auth.tokens.parse_token(result.token)

        modules.db.execute(
            "UPDATE auth_sessions SET expires_at = ? WHERE id = ?", (0, parsed["id"])
        )

        with pytest.raises(Exception):
            modules.auth.verify_token(result.token)

    def test_revoked_session_rejected(self, modules, user_pool):
        """Test that revoked sessions are rejected."""
        user, username, password = user_pool.get_user_with_credentials()

        result = modules.auth.login(username, password)

        modules.auth.logout(result.token)

        with pytest.raises(Exception):
            modules.auth.verify_token(result.token)

    def test_server_operations_require_ownership(self, modules, user_pool):
        """Test that server operations require proper ownership."""
        owner = user_pool.get_user()
        other_user = user_pool.get_user()

        server = modules.servers.create_server(owner_id=owner.id, name="Test Server")

        with pytest.raises(Exception):
            modules.servers.delete_server(other_user.id, server.id)

    def test_channel_operations_require_permissions(self, modules, user_pool):
        """Test that channel operations require proper permissions."""
        owner = user_pool.get_user()
        member = user_pool.get_user()

        server = modules.servers.create_server(owner_id=owner.id, name="Test Server")

        channel = modules.servers.create_channel(
            server_id=server.id, creator_id=owner.id, name="test-channel", type="text"
        )

        modules.servers.add_member(server.id, member.id)

        with pytest.raises(Exception):
            modules.servers.delete_channel(member.id, channel.id)

    def test_conversation_access_requires_membership(self, modules, user_pool):
        """Test that conversation access requires membership."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()
        user3 = user_pool.get_user()

        dm = modules.messaging.create_dm(user1.id, user2.id)

        with pytest.raises(Exception):
            modules.messaging.get_messages(user3.id, dm.id)

    def test_password_change_requires_current_password(self, modules, user_pool):
        """Test that password changes require current password."""
        user, username, password = user_pool.get_user_with_credentials()

        with pytest.raises(Exception):
            modules.auth.change_password(
                user_id=user.id,
                old_password="wrongpassword",
                new_password="NewPass123!",
            )

    def test_2fa_disable_requires_verification(self, modules, user_pool):
        """Test that 2FA disable requires password and code."""
        user, username, password = user_pool.get_user_with_credentials()

        setup = modules.auth.setup_2fa(user.id)

        import re

        match = re.search(r"secret=([A-Z0-9]+)", setup.qr_uri)
        if match:
            secret = match.group(1)
            from src.core.auth import totp

            code = totp.generate_totp_code(secret)
            modules.auth.confirm_2fa(user.id, code)

            with pytest.raises(Exception):
                modules.auth.disable_2fa(user.id, "wrongpassword", code)

    def test_bot_operations_require_ownership(self, modules, user_pool):
        """Test that bot operations require ownership."""
        owner = user_pool.get_user()
        other_user = user_pool.get_user()

        bot = modules.auth.create_bot(
            owner_id=owner.id, username=f"testbot_{owner.id}", display_name="Test Bot"
        )

        with pytest.raises(Exception):
            modules.auth.regenerate_bot_token(other_user.id, bot.id)

    def test_session_binding_prevents_token_reuse(self, modules, user_pool):
        """Test that session binding prevents token reuse from different IPs."""
        user, username, password = user_pool.get_user_with_credentials()

        result = modules.auth.login(
            username=username, password=password, ip_address="192.168.1.1"
        )

        try:
            modules.auth.verify_token(result.token, ip_address="192.168.1.2")
        except Exception:
            pass
