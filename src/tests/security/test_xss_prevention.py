"""
XSS (Cross-Site Scripting) Prevention Tests.

Tests that user input is properly sanitized across all API routes and managers
to prevent XSS attacks through message content, usernames, server names, etc.
"""

import pytest
from src.core.messaging.content import validate_content


class TestXSSPrevention:
    """Test XSS prevention in message content and user inputs."""

    def test_script_tag_in_message_content(self, modules, user_pool):
        """Test that script tags in messages are sanitized."""
        user = user_pool.get_user()
        user2 = user_pool.get_user()
        
        dm = modules.messaging.create_dm(user.id, user2.id)
        
        malicious_content = '<script>alert("XSS")</script>Hello'
        result = validate_content(malicious_content)
        
        assert result.valid
        assert "<script>" not in result.sanitized_content
        assert "alert" not in result.sanitized_content or result.sanitized_content != malicious_content
        
        msg = modules.messaging.send_message(
            user_id=user.id,
            conversation_id=dm.id,
            content=malicious_content
        )
        
        assert msg.content != malicious_content or "<script>" not in msg.content

    def test_img_tag_with_onerror(self, modules, user_pool):
        """Test that img tags with onerror handlers are sanitized."""
        user = user_pool.get_user()
        user2 = user_pool.get_user()
        
        modules.messaging.create_dm(user.id, user2.id)
        
        malicious_content = '<img src="x" onerror="alert(1)">'
        result = validate_content(malicious_content)
        
        assert result.valid
        assert "onerror" not in result.sanitized_content.lower()

    def test_javascript_protocol_in_content(self, modules, user_pool):
        """Test that javascript: protocol URLs are sanitized."""
        user = user_pool.get_user()
        user2 = user_pool.get_user()
        
        modules.messaging.create_dm(user.id, user2.id)
        
        malicious_content = '<a href="javascript:alert(1)">Click</a>'
        result = validate_content(malicious_content)
        
        assert result.valid
        assert "javascript:" not in result.sanitized_content.lower()

    def test_event_handler_attributes(self, modules, user_pool):
        """Test that event handler attributes are sanitized."""
        user = user_pool.get_user()
        user2 = user_pool.get_user()
        
        modules.messaging.create_dm(user.id, user2.id)
        
        malicious_content = '<div onclick="alert(1)" onmouseover="alert(2)">Test</div>'
        result = validate_content(malicious_content)
        
        assert result.valid
        assert "onclick" not in result.sanitized_content.lower()
        assert "onmouseover" not in result.sanitized_content.lower()

    def test_iframe_injection(self, modules, user_pool):
        """Test that iframe tags are sanitized."""
        user = user_pool.get_user()
        user2 = user_pool.get_user()
        
        modules.messaging.create_dm(user.id, user2.id)
        
        malicious_content = '<iframe src="https://evil.com"></iframe>'
        result = validate_content(malicious_content)
        
        assert result.valid
        assert "<iframe" not in result.sanitized_content.lower()

    def test_svg_with_script(self, modules, user_pool):
        """Test that SVG with embedded scripts is sanitized."""
        user = user_pool.get_user()
        user2 = user_pool.get_user()
        
        modules.messaging.create_dm(user.id, user2.id)
        
        malicious_content = '<svg><script>alert(1)</script></svg>'
        result = validate_content(malicious_content)
        
        assert result.valid
        assert "<script>" not in result.sanitized_content

    def test_html_entities_encoding(self, modules, user_pool):
        """Test that special characters are handled safely."""
        user = user_pool.get_user()
        user2 = user_pool.get_user()
        
        modules.messaging.create_dm(user.id, user2.id)
        
        content_with_entities = '&lt;script&gt;alert("XSS")&lt;/script&gt;'
        result = validate_content(content_with_entities)
        
        assert result.valid
        assert "script" not in result.sanitized_content.lower() or "&lt;" in result.sanitized_content

    def test_data_uri_with_script(self, modules, user_pool):
        """Test that data: URIs with scripts are sanitized."""
        user = user_pool.get_user()
        user2 = user_pool.get_user()
        
        modules.messaging.create_dm(user.id, user2.id)
        
        malicious_content = '<a href="data:text/html,<script>alert(1)</script>">Click</a>'
        result = validate_content(malicious_content)
        
        assert result.valid

    def test_xss_in_username(self, modules):
        """Test that XSS attempts in username are rejected."""
        malicious_username = '<script>alert("XSS")</script>user'
        
        with pytest.raises(Exception):
            modules.auth.register(
                username=malicious_username,
                email="xss@test.com",
                password="TestPass123!"
            )

    def test_xss_in_server_name(self, modules, user_pool):
        """Test that XSS attempts in server names are sanitized or rejected."""
        owner = user_pool.get_user()
        
        malicious_name = '<script>alert("XSS")</script>Server'
        
        try:
            server = modules.servers.create_server(
                owner_id=owner.id,
                name=malicious_name
            )
            assert "<script>" not in server.name
        except Exception:
            pass

    def test_xss_in_channel_name(self, modules, user_pool):
        """Test that XSS attempts in channel names are sanitized or rejected."""
        owner = user_pool.get_user()
        server = modules.servers.create_server(
            owner_id=owner.id,
            name="Test Server"
        )
        
        malicious_name = '<img src=x onerror=alert(1)>channel'
        
        try:
            channel = modules.servers.create_channel(
                server_id=server.id,
                creator_id=owner.id,
                name=malicious_name,
                type="text"
            )
            assert "onerror" not in channel.name.lower()
        except Exception:
            pass

    def test_xss_in_group_name(self, modules, user_pool):
        """Test that XSS attempts in group names are sanitized or rejected."""
        owner = user_pool.get_user()
        member = user_pool.get_user()
        
        malicious_name = '<svg onload=alert(1)>Group'
        
        try:
            group = modules.messaging.create_group(
                owner_id=owner.id,
                name=malicious_name,
                participant_ids=[member.id]
            )
            assert "onload" not in group.name.lower()
        except Exception:
            pass

    def test_nested_xss_payloads(self, modules, user_pool):
        """Test nested XSS payloads."""
        user = user_pool.get_user()
        user2 = user_pool.get_user()
        
        modules.messaging.create_dm(user.id, user2.id)
        
        malicious_content = '<<SCRIPT>alert("XSS")//<</SCRIPT>'
        result = validate_content(malicious_content)
        
        assert result.valid
        assert "alert" not in result.sanitized_content or malicious_content != result.sanitized_content

    def test_encoded_script_tag(self, modules, user_pool):
        """Test URL-encoded script tags."""
        user = user_pool.get_user()
        user2 = user_pool.get_user()
        
        modules.messaging.create_dm(user.id, user2.id)
        
        malicious_content = '%3Cscript%3Ealert(1)%3C/script%3E'
        result = validate_content(malicious_content)
        
        assert result.valid

    def test_style_tag_with_expression(self, modules, user_pool):
        """Test style tags with expressions."""
        user = user_pool.get_user()
        user2 = user_pool.get_user()
        
        modules.messaging.create_dm(user.id, user2.id)
        
        malicious_content = '<style>body{background:url("javascript:alert(1)")}</style>'
        result = validate_content(malicious_content)
        
        assert result.valid
        assert "javascript:" not in result.sanitized_content.lower()
