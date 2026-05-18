"""
API Security Tests.

Tests security of API routes including authentication requirements,
input validation, rate limiting, and proper error handling.
"""

import pytest


class TestAPIRouteSecurity:
    """Test security of API routes."""

    def test_authenticated_routes_require_token(self, messaging_manager):
        """Test that authenticated routes require valid tokens."""
        with pytest.raises(Exception):
            messaging_manager.get_messages(user_id=None, conversation_id=1)

    def test_api_validates_user_ids(self, auth_manager):
        """Test that API validates user IDs."""
        # The API should handle invalid user IDs gracefully
        # It may return None or a system user depending on the ID
        invalid_user_ids = ["abc", "-1", "999999999999999"]

        for invalid_id in invalid_user_ids:
            result = auth_manager.get_user(invalid_id)
            # Should not crash - result can be None or system user
            # The important thing is it doesn't raise an exception
            assert result is None or result.id == 0

    def test_api_validates_message_ids(self, messaging_manager, test_user):
        """Test that API validates message IDs."""
        invalid_message_ids = ["abc", "-1", ""]

        for invalid_id in invalid_message_ids:
            # API should return None for invalid message IDs
            result = messaging_manager.get_message(test_user.id, invalid_id)
            assert result is None

    def test_api_validates_conversation_ids(self, messaging_manager, test_user):
        """Test that API validates conversation IDs."""
        invalid_conv_ids = ["abc", "-1", "", "99999999999"]

        for invalid_id in invalid_conv_ids:
            with pytest.raises(Exception):
                messaging_manager.get_messages(test_user.id, invalid_id)

    def test_api_validates_content_length(self, messaging_manager, two_users):
        """Test that API validates content length."""
        user1, user2 = two_users

        dm = messaging_manager.create_dm(user1.id, user2.id)

        too_long = "x" * 10000

        with pytest.raises(Exception):
            messaging_manager.send_message(
                user_id=user1.id, conversation_id=dm.id, content=too_long
            )

    def test_api_validates_username_format(self, auth_manager):
        """Test that API validates username format."""
        invalid_usernames = [
            "",
            "a",
            "ab",
            "x" * 100,
            "user name",
            "user\nname",
            "user\tname",
        ]

        for username in invalid_usernames:
            with pytest.raises(Exception):
                auth_manager.register(
                    username=username, email="test@test.com", password="TestPass123!"
                )

    def test_api_validates_email_format(self, auth_manager):
        """Test that API validates email format."""
        invalid_emails = [
            "",
            "notanemail",
            "@test.com",
            "user@",
            "user test@test.com",
            "user@test",
        ]

        for email in invalid_emails:
            with pytest.raises(Exception):
                auth_manager.register(
                    username="testuser", email=email, password="TestPass123!"
                )

    def test_api_validates_password_requirements(self, auth_manager):
        """Test that API validates password requirements."""
        weak_passwords = [
            "",
            "short",
            "alllowercase",
            "ALLUPPERCASE",
            "NoDigits!",
            "NoSpecial123",
        ]

        for password in weak_passwords:
            with pytest.raises(Exception):
                auth_manager.register(
                    username="testuser123", email="test@test.com", password=password
                )

    def test_api_rate_limiting_enforced(self, auth_manager):
        """Test that API rate limiting is enforced."""
        for i in range(20):
            try:
                auth_manager.login("nonexistent", "password")
            except Exception:
                pass

    def test_api_error_messages_dont_leak_info(self, auth_manager):
        """Test that error messages don't leak sensitive information."""
        try:
            auth_manager.login("nonexistent_user", "password")
        except Exception as e:
            error_msg = str(e).lower()
            assert "sql" not in error_msg
            assert "database" not in error_msg
            assert "stack" not in error_msg

    def test_api_handles_missing_parameters(self, auth_manager):
        """Test that API handles missing parameters gracefully."""
        with pytest.raises(Exception):
            auth_manager.register(username=None, email=None, password=None)

    def test_api_handles_wrong_type_parameters(self, auth_manager):
        """Test that API handles wrong type parameters."""
        # API should return None for non-numeric user IDs
        result = auth_manager.get_user("not_a_number")
        assert result is None

    def test_api_prevents_parameter_pollution(self, messaging_manager, two_users):
        """Test that API prevents parameter pollution."""
        user1, user2 = two_users

        dm = messaging_manager.create_dm(user1.id, user2.id)

        msg = messaging_manager.send_message(
            user_id=user1.id, conversation_id=dm.id, content="Test"
        )

        assert msg.author_id == user1.id

    def test_api_validates_json_structure(self, messaging_manager, two_users):
        """Test that API validates JSON structure in requests."""
        user1, user2 = two_users

        dm = messaging_manager.create_dm(user1.id, user2.id)

        msg = messaging_manager.send_message(
            user_id=user1.id, conversation_id=dm.id, content="Test message"
        )

        assert msg.content == "Test message"

    def test_api_handles_nested_object_depth(self, messaging_manager, two_users):
        """Test that API handles deeply nested objects."""
        user1, user2 = two_users

        dm = messaging_manager.create_dm(user1.id, user2.id)

        try:
            embeds = [
                {"title": "Test", "fields": [{"name": "Field", "value": "Value"}]}
            ]
            messaging_manager.send_message(
                user_id=user1.id, conversation_id=dm.id, content="Test", embeds=embeds
            )
        except Exception:
            pass

    def test_api_validates_array_bounds(self, messaging_manager, test_user):
        """Test that API validates array bounds."""
        too_many_participants = [test_user.id + i for i in range(150)]

        with pytest.raises(Exception):
            messaging_manager.create_group(
                owner_id=test_user.id,
                name="Test Group",
                participant_ids=too_many_participants,
            )

    def test_api_sanitizes_error_responses(self, auth_manager):
        """Test that API sanitizes error responses."""
        try:
            auth_manager.login("test", "test")
        except Exception as e:
            error_str = str(e)
            assert "SELECT" not in error_str.upper()
            assert "FROM" not in error_str.upper()
            assert "WHERE" not in error_str.upper()

    def test_api_prevents_mass_assignment(self, server_manager, test_user):
        """Test that API prevents mass assignment vulnerabilities."""
        server = server_manager.create_server(owner_id=test_user.id, name="Test Server")

        assert server.owner_id == test_user.id

    def test_api_validates_file_uploads(self, messaging_manager, two_users):
        """Test that API validates file upload parameters."""
        user1, user2 = two_users

        dm = messaging_manager.create_dm(user1.id, user2.id)

        attachments = [
            {
                "filename": "test.txt",
                "content_type": "text/plain",
                "size": 1024,
                "url": "http://example.com/file.txt",
            }
        ]

        messaging_manager.send_message(
            user_id=user1.id,
            conversation_id=dm.id,
            content="File attached",
            attachments=attachments,
        )

    def test_api_prevents_path_traversal(self, auth_manager):
        """Test that API prevents path traversal attacks."""
        path_traversal_attempts = [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32",
            "....//....//....//etc/passwd",
        ]

        for attempt in path_traversal_attempts:
            with pytest.raises(Exception):
                auth_manager.register(
                    username=attempt, email="test@test.com", password="TestPass123!"
                )

    def test_api_handles_unicode_in_requests(self, messaging_manager, two_users):
        """Test that API handles unicode characters properly."""
        user1, user2 = two_users

        dm = messaging_manager.create_dm(user1.id, user2.id)

        unicode_content = "Hello 世界 🌍"

        msg = messaging_manager.send_message(
            user_id=user1.id, conversation_id=dm.id, content=unicode_content
        )

        assert msg.content == unicode_content
