"""
Tests for XSS prevention and URL validation.
"""

import pytest
from src.core.embeds import (
    EmbedValidationError,
    EmbedSanitizationError,
)


class TestXssPrevention:
    """Tests for XSS prevention in embed content."""

    def test_script_tag_in_title_rejected(self, db_and_modules):
        """Test script tag in title is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"xss1_{unique_id}",
            email=f"xss1_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="<script>alert('xss')</script>"
            )

    def test_script_tag_in_description_rejected(self, db_and_modules):
        """Test script tag in description is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"xss2_{unique_id}",
            email=f"xss2_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                description="Hello <script>alert('xss')</script> World"
            )

    def test_javascript_event_handler_rejected(self, db_and_modules):
        """Test JavaScript event handler is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"xss3_{unique_id}",
            email=f"xss3_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Click me onclick=alert('xss')"
            )

    def test_iframe_tag_rejected(self, db_and_modules):
        """Test iframe tag is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"xss4_{unique_id}",
            email=f"xss4_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                description="<iframe src='https://evil.com'></iframe>"
            )

    def test_object_tag_rejected(self, db_and_modules):
        """Test object tag is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"xss5_{unique_id}",
            email=f"xss5_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                description="<object data='evil.swf'></object>"
            )

    def test_embed_tag_rejected(self, db_and_modules):
        """Test embed tag is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"xss6_{unique_id}",
            email=f"xss6_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                description="<embed src='evil.swf'>"
            )

    def test_xss_in_field_name_rejected(self, db_and_modules):
        """Test XSS in field name is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"xss7_{unique_id}",
            email=f"xss7_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Test",
                fields=[{"name": "<script>alert('xss')</script>", "value": "test"}]
            )

    def test_xss_in_field_value_rejected(self, db_and_modules):
        """Test XSS in field value is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"xss8_{unique_id}",
            email=f"xss8_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Test",
                fields=[{"name": "test", "value": "<script>alert('xss')</script>"}]
            )

    def test_xss_in_footer_rejected(self, db_and_modules):
        """Test XSS in footer is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"xss9_{unique_id}",
            email=f"xss9_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Test",
                footer={"text": "<script>alert('xss')</script>"}
            )

    def test_xss_in_author_rejected(self, db_and_modules):
        """Test XSS in author is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"xss10_{unique_id}",
            email=f"xss10_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Test",
                author={"name": "<script>alert('xss')</script>"}
            )


class TestUrlSanitization:
    """Tests for URL sanitization."""

    def test_javascript_url_rejected(self, db_and_modules):
        """Test JavaScript URL is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"san1_{unique_id}",
            email=f"san1_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Test",
                url="javascript:alert('xss')"
            )

    def test_data_url_rejected(self, db_and_modules):
        """Test data URL is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"san2_{unique_id}",
            email=f"san2_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Test",
                url="data:text/html,<script>alert('xss')</script>"
            )

    def test_vbscript_url_rejected(self, db_and_modules):
        """Test VBScript URL is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"san3_{unique_id}",
            email=f"san3_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Test",
                url="vbscript:msgbox('xss')"
            )

    def test_javascript_url_in_image_rejected(self, db_and_modules):
        """Test JavaScript URL in image is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"san4_{unique_id}",
            email=f"san4_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Test",
                image={"url": "javascript:alert('xss')"}
            )

    def test_data_url_in_thumbnail_rejected(self, db_and_modules):
        """Test data URL in thumbnail is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"san5_{unique_id}",
            email=f"san5_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Test",
                thumbnail={"url": "data:image/png;base64,abc"}
            )

    def test_javascript_url_in_author_rejected(self, db_and_modules):
        """Test JavaScript URL in author is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"san6_{unique_id}",
            email=f"san6_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Test",
                author={"name": "Test", "url": "javascript:alert('xss')"}
            )

    def test_javascript_url_in_footer_icon_rejected(self, db_and_modules):
        """Test JavaScript URL in footer icon is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"san7_{unique_id}",
            email=f"san7_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Test",
                footer={"text": "Test", "icon_url": "javascript:alert('xss')"}
            )


class TestSanitizeContentFunction:
    """Tests for sanitize_embed_content function."""

    def test_sanitize_safe_content(self, db_and_modules):
        """Test sanitizing safe content returns unchanged."""
        db, auth, messaging, servers, embeds = db_and_modules

        result = embeds.sanitize_embed_content("Hello World")
        assert result == "Hello World"

    def test_sanitize_content_with_script(self, db_and_modules):
        """Test sanitizing content with script raises error."""
        db, auth, messaging, servers, embeds = db_and_modules

        with pytest.raises(EmbedSanitizationError):
            embeds.sanitize_embed_content("<script>alert('xss')</script>")

    def test_sanitize_content_with_event_handler(self, db_and_modules):
        """Test sanitizing content with event handler raises error."""
        db, auth, messaging, servers, embeds = db_and_modules

        with pytest.raises(EmbedSanitizationError):
            embeds.sanitize_embed_content("onclick=alert('xss')")


class TestCaseInsensitiveXss:
    """Tests for case-insensitive XSS detection."""

    def test_uppercase_script_rejected(self, db_and_modules):
        """Test uppercase SCRIPT tag is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"case1_{unique_id}",
            email=f"case1_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="<SCRIPT>alert('xss')</SCRIPT>"
            )

    def test_mixed_case_script_rejected(self, db_and_modules):
        """Test mixed case ScRiPt tag is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"case2_{unique_id}",
            email=f"case2_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="<ScRiPt>alert('xss')</ScRiPt>"
            )

    def test_uppercase_javascript_url_rejected(self, db_and_modules):
        """Test uppercase JAVASCRIPT URL is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"case3_{unique_id}",
            email=f"case3_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Test",
                url="JAVASCRIPT:alert('xss')"
            )
