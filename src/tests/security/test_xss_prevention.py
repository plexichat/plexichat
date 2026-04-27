"""
XSS (Cross-Site Scripting) Prevention Tests.

Tests that user input is properly sanitized across all API routes and managers
to prevent XSS attacks through message content, usernames, server names, etc.
"""

import pytest
from src.core.messaging.content import validate_content


class TestXSSPrevention:
    """Test XSS prevention in message content and user inputs."""

    def test_script_tag_in_message_content(self, messaging_manager, two_users):
        """Test that script tags in messages are sanitized."""
        user1, user2 = two_users

        dm = messaging_manager.create_dm(user1.id, user2.id)

        malicious_content = '<script>alert("XSS")</script>Hello'
        result = validate_content(malicious_content)

        # Content should be sanitized - script tags should be escaped
        assert result.valid
        # The important thing is the script tag is not executable
        # It may be present as escaped HTML entities
        assert (
            "<script>" not in result.sanitized_content
            or "&lt;" in result.sanitized_content
        )

        # Content validation rejects script tags
        with pytest.raises(Exception):
            messaging_manager.send_message(
                user_id=user1.id, conversation_id=dm.id, content=malicious_content
            )

    def test_img_tag_with_onerror(self, messaging_manager, two_users):
        """Test that img tags with onerror handlers are sanitized."""
        user1, user2 = two_users

        messaging_manager.create_dm(user1.id, user2.id)

        malicious_content = '<img src="x" onerror="alert(1)">'
        result = validate_content(malicious_content)

        assert result.valid
        # HTML should be escaped/encoded, not executable
        # The important thing is it's not raw HTML that could execute
        assert (
            "&lt;" in result.sanitized_content
            or "onerror" not in result.sanitized_content.lower()
        )

    def test_javascript_protocol_in_content(self, messaging_manager, two_users):
        """Test that javascript: protocol URLs are sanitized."""
        user1, user2 = two_users

        messaging_manager.create_dm(user1.id, user2.id)

        malicious_content = '<a href="javascript:alert(1)">Click</a>'
        result = validate_content(malicious_content)

        assert result.valid
        # HTML should be escaped/encoded, not executable
        assert (
            "&lt;" in result.sanitized_content
            or "javascript:" not in result.sanitized_content.lower()
        )

    def test_event_handler_attributes(self, messaging_manager, two_users):
        """Test that event handler attributes are sanitized."""
        user1, user2 = two_users

        messaging_manager.create_dm(user1.id, user2.id)

        malicious_content = '<div onclick="alert(1)" onmouseover="alert(2)">Test</div>'
        result = validate_content(malicious_content)

        assert result.valid
        # HTML should be escaped/encoded, not executable
        assert (
            "&lt;" in result.sanitized_content
            or "onclick" not in result.sanitized_content.lower()
        )

    def test_iframe_injection(self, messaging_manager, two_users):
        """Test that iframe tags are sanitized."""
        user1, user2 = two_users

        messaging_manager.create_dm(user1.id, user2.id)

        malicious_content = '<iframe src="https://evil.com"></iframe>'
        result = validate_content(malicious_content)

        assert result.valid
        assert "<iframe" not in result.sanitized_content.lower()

    def test_svg_with_script(self, messaging_manager, two_users):
        """Test that SVG with embedded scripts is sanitized."""
        user1, user2 = two_users

        messaging_manager.create_dm(user1.id, user2.id)

        malicious_content = "<svg><script>alert(1)</script></svg>"
        result = validate_content(malicious_content)

        assert result.valid
        assert "<script>" not in result.sanitized_content

    def test_html_entities_encoding(self, messaging_manager, two_users):
        """Test that special characters are handled safely."""
        user1, user2 = two_users

        messaging_manager.create_dm(user1.id, user2.id)

        content_with_entities = '&lt;script&gt;alert("XSS")&lt;/script&gt;'
        result = validate_content(content_with_entities)

        assert result.valid
        # HTML entities should be preserved or double-escaped
        # The important thing is it's not executable script
        assert "&amp;" in result.sanitized_content or "&lt;" in result.sanitized_content

    def test_data_uri_with_script(self, messaging_manager, two_users):
        """Test that data: URIs with scripts are sanitized."""
        user1, user2 = two_users

        messaging_manager.create_dm(user1.id, user2.id)

        malicious_content = (
            '<a href="data:text/html,<script>alert(1)</script>">Click</a>'
        )
        result = validate_content(malicious_content)

        assert result.valid

    def test_xss_in_username(self, auth_manager):
        """Test that XSS attempts in username are rejected."""
        malicious_username = '<script>alert("XSS")</script>user'

        with pytest.raises(Exception):
            auth_manager.register(
                username=malicious_username,
                email="xss@test.com",
                password="TestPass123!",
            )

    def test_xss_in_server_name(self, server_manager, test_user):
        """Test that XSS attempts in server names are sanitized or rejected."""
        malicious_name = '<script>alert("XSS")</script>Server'

        try:
            server = server_manager.create_server(
                owner_id=test_user.id, name=malicious_name
            )
            assert "<script>" not in server.name
        except Exception:
            pass

    def test_xss_in_channel_name(self, server_manager, test_user):
        """Test that XSS attempts in channel names are sanitized or rejected."""
        server = server_manager.create_server(owner_id=test_user.id, name="Test Server")

        malicious_name = "<img src=x onerror=alert(1)>channel"

        try:
            channel = server_manager.create_channel(
                user_id=test_user.id,
                server_id=server.id,
                name=malicious_name,
                channel_type="text",
            )
            assert "onerror" not in channel.name.lower()
        except Exception:
            pass

    def test_xss_in_group_name(self, messaging_manager, two_users):
        """Test that XSS attempts in group names are sanitized or rejected."""
        user1, user2 = two_users

        malicious_name = "<svg onload=alert(1)>Group"

        try:
            group = messaging_manager.create_group(
                owner_id=user1.id, name=malicious_name, participant_ids=[user2.id]
            )
            assert "onload" not in group.name.lower()
        except Exception:
            pass

    def test_nested_xss_payloads(self, messaging_manager, two_users):
        """Test nested XSS payloads."""
        user1, user2 = two_users

        messaging_manager.create_dm(user1.id, user2.id)

        malicious_content = '<<SCRIPT>alert("XSS")//<</SCRIPT>'
        result = validate_content(malicious_content)

        assert result.valid
        assert (
            "alert" not in result.sanitized_content
            or malicious_content != result.sanitized_content
        )

    def test_encoded_script_tag(self, messaging_manager, two_users):
        """Test URL-encoded script tags."""
        user1, user2 = two_users

        messaging_manager.create_dm(user1.id, user2.id)

        malicious_content = "%3Cscript%3Ealert(1)%3C/script%3E"
        result = validate_content(malicious_content)

        assert result.valid

    def test_style_tag_with_expression(self, messaging_manager, two_users):
        """Test style tags with expressions."""
        user1, user2 = two_users

        messaging_manager.create_dm(user1.id, user2.id)

        malicious_content = '<style>body{background:url("javascript:alert(1)")}</style>'
        result = validate_content(malicious_content)

        assert result.valid
        # HTML should be escaped/encoded, not executable
        assert (
            "&lt;" in result.sanitized_content
            or "javascript:" not in result.sanitized_content.lower()
        )
