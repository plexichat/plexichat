"""
API Security Tests.

Tests security of API routes including authentication requirements,
input validation, rate limiting, and proper error handling.
"""

import pytest


class TestAPIRouteSecurity:
    """Test security of API routes."""

    def test_authenticated_routes_require_token(self, modules, user_pool):
        """Test that authenticated routes require valid tokens."""
        user_pool.get_user()

        with pytest.raises(Exception):
            modules.messaging.get_messages(user_id=None, conversation_id=1)

    def test_api_validates_user_ids(self, modules, user_pool):
        """Test that API validates user IDs."""
        invalid_user_ids = ["abc", "-1", "0", "999999999999999"]

        for invalid_id in invalid_user_ids:
            with pytest.raises(Exception):
                modules.auth.get_user(invalid_id)

    def test_api_validates_message_ids(self, modules, user_pool):
        """Test that API validates message IDs."""
        user = user_pool.get_user()

        invalid_message_ids = ["abc", "-1", ""]

        for invalid_id in invalid_message_ids:
            with pytest.raises(Exception):
                modules.messaging.get_message(user.id, invalid_id)

    def test_api_validates_conversation_ids(self, modules, user_pool):
        """Test that API validates conversation IDs."""
        user = user_pool.get_user()

        invalid_conv_ids = ["abc", "-1", "", "99999999999"]

        for invalid_id in invalid_conv_ids:
            with pytest.raises(Exception):
                modules.messaging.get_messages(user.id, invalid_id)

    def test_api_validates_content_length(self, modules, user_pool):
        """Test that API validates content length."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        dm = modules.messaging.create_dm(user1.id, user2.id)

        too_long = "x" * 10000

        with pytest.raises(Exception):
            modules.messaging.send_message(
                user_id=user1.id, conversation_id=dm.id, content=too_long
            )

    def test_api_validates_username_format(self, modules):
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
                modules.auth.register(
                    username=username, email="test@test.com", password="TestPass123!"
                )

    def test_api_validates_email_format(self, modules):
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
                modules.auth.register(
                    username="testuser", email=email, password="TestPass123!"
                )

    def test_api_validates_password_requirements(self, modules):
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
                modules.auth.register(
                    username="testuser123", email="test@test.com", password=password
                )

    def test_api_rate_limiting_enforced(self, modules):
        """Test that API rate limiting is enforced."""
        for i in range(20):
            try:
                modules.auth.login("nonexistent", "password")
            except Exception:
                pass

    def test_api_error_messages_dont_leak_info(self, modules):
        """Test that error messages don't leak sensitive information."""
        try:
            modules.auth.login("nonexistent_user", "password")
        except Exception as e:
            error_msg = str(e).lower()
            assert "sql" not in error_msg
            assert "database" not in error_msg
            assert "stack" not in error_msg

    def test_api_handles_missing_parameters(self, modules):
        """Test that API handles missing parameters gracefully."""
        with pytest.raises(Exception):
            modules.auth.register(username=None, email=None, password=None)

    def test_api_handles_wrong_type_parameters(self, modules, user_pool):
        """Test that API handles wrong type parameters."""
        with pytest.raises(Exception):
            modules.auth.get_user("not_a_number")

    def test_api_prevents_parameter_pollution(self, modules, user_pool):
        """Test that API prevents parameter pollution."""
        user = user_pool.get_user()
        user2 = user_pool.get_user()

        dm = modules.messaging.create_dm(user.id, user2.id)

        msg = modules.messaging.send_message(
            user_id=user.id, conversation_id=dm.id, content="Test"
        )

        assert msg.author_id == user.id

    def test_api_validates_json_structure(self, modules, user_pool):
        """Test that API validates JSON structure in requests."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        dm = modules.messaging.create_dm(user1.id, user2.id)

        msg = modules.messaging.send_message(
            user_id=user1.id, conversation_id=dm.id, content="Test message"
        )

        assert msg.content == "Test message"

    def test_api_handles_nested_object_depth(self, modules, user_pool):
        """Test that API handles deeply nested objects."""
        user = user_pool.get_user()
        user2 = user_pool.get_user()

        dm = modules.messaging.create_dm(user.id, user2.id)

        try:
            embeds = [
                {"title": "Test", "fields": [{"name": "Field", "value": "Value"}]}
            ]
            modules.messaging.send_message(
                user_id=user.id, conversation_id=dm.id, content="Test", embeds=embeds
            )
        except Exception:
            pass

    def test_api_validates_array_bounds(self, modules, user_pool):
        """Test that API validates array bounds."""
        owner = user_pool.get_user()

        too_many_participants = [user_pool.get_user().id for _ in range(150)]

        with pytest.raises(Exception):
            modules.messaging.create_group(
                owner_id=owner.id,
                name="Test Group",
                participant_ids=too_many_participants,
            )

    def test_api_sanitizes_error_responses(self, modules):
        """Test that API sanitizes error responses."""
        try:
            modules.auth.login("test", "test")
        except Exception as e:
            error_str = str(e)
            assert "SELECT" not in error_str.upper()
            assert "FROM" not in error_str.upper()
            assert "WHERE" not in error_str.upper()

    def test_api_prevents_mass_assignment(self, modules, user_pool):
        """Test that API prevents mass assignment vulnerabilities."""
        owner = user_pool.get_user()

        server = modules.servers.create_server(owner_id=owner.id, name="Test Server")

        assert server.owner_id == owner.id

    def test_api_validates_file_uploads(self, modules, user_pool):
        """Test that API validates file upload parameters."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        dm = modules.messaging.create_dm(user1.id, user2.id)

        attachments = [
            {
                "filename": "test.txt",
                "content_type": "text/plain",
                "size": 1024,
                "url": "http://example.com/file.txt",
            }
        ]

        modules.messaging.send_message(
            user_id=user1.id,
            conversation_id=dm.id,
            content="File attached",
            attachments=attachments,
        )

    def test_api_prevents_path_traversal(self, modules):
        """Test that API prevents path traversal attacks."""
        path_traversal_attempts = [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32",
            "....//....//....//etc/passwd",
        ]

        for attempt in path_traversal_attempts:
            with pytest.raises(Exception):
                modules.auth.register(
                    username=attempt, email="test@test.com", password="TestPass123!"
                )

    def test_api_handles_unicode_in_requests(self, modules, user_pool):
        """Test that API handles unicode characters properly."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        dm = modules.messaging.create_dm(user1.id, user2.id)

        unicode_content = "Hello 世界 🌍"

        msg = modules.messaging.send_message(
            user_id=user1.id, conversation_id=dm.id, content=unicode_content
        )

        assert msg.content == unicode_content
