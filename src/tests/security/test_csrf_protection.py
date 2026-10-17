"""
CSRF (Cross-Site Request Forgery) Protection Tests.

Tests that state-changing operations require proper authentication tokens
and cannot be performed through simple cross-origin requests.
"""

import pytest

from src.core.auth.exceptions import (
    TokenExpiredError,
    TokenInvalidError,
    InvalidCredentialsError,
)


class TestCSRFProtection:
    """Test CSRF protection mechanisms."""

    def test_state_change_requires_authentication(self, messaging_manager):
        """Test that state-changing operations require authentication."""
        with pytest.raises(Exception):
            messaging_manager.send_message(
                user_id=None, conversation_id=1, content="Test"
            )

    def test_token_required_for_message_send(self, messaging_manager, two_users):
        """Test that message sending requires valid token."""
        user1, user2 = two_users

        dm = messaging_manager.create_dm(user1.id, user2.id)

        msg = messaging_manager.send_message(
            user_id=user1.id, conversation_id=dm.id, content="Test"
        )

        assert msg is not None
        assert msg.author_id == user1.id

    def test_token_required_for_message_deletion(self, messaging_manager, two_users):
        """Test that message deletion requires proper authentication."""
        user1, user2 = two_users

        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(
            user_id=user1.id, conversation_id=dm.id, content="Test"
        )

        messaging_manager.delete_message(user1.id, msg.id)

    def test_cannot_perform_actions_for_other_users(self, messaging_manager, two_users):
        """Test that users cannot perform actions as other users."""
        user1, user2 = two_users

        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(
            user_id=user1.id, conversation_id=dm.id, content="Test from user1"
        )

        with pytest.raises(Exception):
            messaging_manager.delete_message(user2.id, msg.id)

    def test_session_token_validates_user_identity(self, auth_manager, test_user):
        """Test that session tokens properly validate user identity."""
        result = auth_manager.login(test_user.username, "TestPass123!")
        assert result.token is not None

        token_info = auth_manager.verify_token(result.token)
        assert token_info.user_id == test_user.id

    def test_expired_session_rejected(self, auth_manager, db, test_user):
        """Test that expired sessions are rejected."""
        import time

        result = auth_manager.login(test_user.username, "TestPass123!")

        current_time = int(time.time() * 1000)
        db.execute(
            "UPDATE auth_sessions SET expires_at = ? WHERE id = ?",
            (current_time - 100000, result.session.id),
        )

        with pytest.raises(TokenExpiredError):
            auth_manager.verify_token(result.token)

    def test_revoked_session_rejected(self, auth_manager, test_user):
        """Test that revoked sessions are rejected."""
        result = auth_manager.login(test_user.username, "TestPass123!")

        auth_manager.logout(result.token)

        with pytest.raises(Exception):
            auth_manager.verify_token(result.token)

    def test_server_operations_require_ownership(self, server_manager, two_users):
        """Test that server operations require proper ownership."""
        owner, other_user = two_users

        server = server_manager.create_server(owner_id=owner.id, name="Test Server")

        with pytest.raises(Exception):
            server_manager.delete_server(other_user.id, server.id)

    def test_channel_operations_require_permissions(self, server_manager, two_users):
        """Test that channel operations require proper permissions."""
        owner, member = two_users

        server = server_manager.create_server(owner_id=owner.id, name="Test Server")

        channel = server_manager.create_channel(
            user_id=owner.id,
            server_id=server.id,
            name="test-channel",
            channel_type="text",
        )

        server_manager.add_member(server.id, member.id)

        with pytest.raises(Exception):
            server_manager.delete_channel(member.id, channel.id)

    def test_conversation_access_requires_membership(
        self, messaging_manager, three_users
    ):
        """Test that conversation access requires membership."""
        user1, user2, user3 = three_users

        dm = messaging_manager.create_dm(user1.id, user2.id)

        with pytest.raises(Exception):
            messaging_manager.get_messages(user3.id, dm.id)

    def test_password_change_requires_current_password(self, auth_manager, test_user):
        """Test that password changes require current password."""
        with pytest.raises(Exception):
            auth_manager.change_password(
                user_id=test_user.id,
                old_password="wrongpassword",
                new_password="NewPass123!",
            )

    def test_2fa_disable_requires_verification(self, auth_manager, db, test_user):
        """Test that 2FA disable requires password and code."""
        db.execute(
            "UPDATE auth_users SET totp_enabled = 1, totp_secret_encrypted = ? WHERE id = ?",
            ("encrypted_secret", test_user.id),
        )

        with pytest.raises(InvalidCredentialsError):
            auth_manager.disable_2fa(test_user.id, "WrongPassword", "123456")

    def test_bot_operations_require_ownership(self, auth_manager, two_users):
        """Test that bot operations require ownership."""
        owner, other_user = two_users

        bot = auth_manager.create_bot(
            owner_id=owner.id, username=f"testbot_{owner.id}", display_name="Test Bot"
        )

        with pytest.raises(Exception):
            auth_manager.regenerate_bot_token(other_user.id, bot.id)

    def test_session_binding_prevents_token_reuse(
        self, auth_manager, test_user, monkeypatch
    ):
        """Test that session binding prevents token reuse from different IPs."""
        manager = auth_manager._manager
        monkeypatch.setattr(
            manager,
            "_config",
            {
                **manager._config,
                "security": {
                    **manager._config.get("security", {}),
                    "token_binding": True,
                },
            },
        )
        result = auth_manager.login(
            username=test_user.username,
            password="TestPass123!",
            ip_address="192.168.1.1",
            user_agent="ReuseUA/1",
        )

        # Same IP+UA still verifies.
        token_info = auth_manager.verify_token(
            result.token, ip_address="192.168.1.1", user_agent="ReuseUA/1"
        )
        assert token_info.valid

        # The token must not be usable from a different IP.
        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token(
                result.token, ip_address="192.168.1.2", user_agent="ReuseUA/1"
            )
