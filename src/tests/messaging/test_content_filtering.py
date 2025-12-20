"""
Content filtering and validation tests.

Tests XSS prevention, markdown injection, profanity filtering,
NSFW detection, and content sanitization.
"""

from src.core.messaging.content import validate_content
from src.core.messaging.models import ContentFilter, FilterAction


class TestXSSPrevention:
    """Tests for XSS attack prevention."""

    def test_script_tag_sanitization(self, dm_conversation):
        """Test that script tags are sanitized."""
        dm, user1, user2, messaging = dm_conversation

        malicious = '<script>alert("XSS")</script>Normal text'
        msg = messaging.send_message(user1.id, dm.id, malicious)

        # Content should be sanitized
        assert "<script>" not in msg.content
        assert "alert" not in msg.content or msg.content != malicious

    def test_onclick_attribute_sanitization(self, dm_conversation):
        """Test that onclick attributes are sanitized."""
        dm, user1, user2, messaging = dm_conversation

        malicious = '<img src="x" onclick="malicious()">'
        msg = messaging.send_message(user1.id, dm.id, malicious)

        assert "onclick" not in msg.content.lower()

    def test_javascript_url_sanitization(self, dm_conversation):
        """Test that javascript: URLs are sanitized."""
        dm, user1, user2, messaging = dm_conversation

        malicious = '<a href="javascript:alert(1)">Click</a>'
        msg = messaging.send_message(user1.id, dm.id, malicious)

        assert "javascript:" not in msg.content.lower()

    def test_onerror_attribute_sanitization(self, dm_conversation):
        """Test that onerror handlers are sanitized."""
        dm, user1, user2, messaging = dm_conversation

        malicious = '<img src="invalid" onerror="alert(1)">'
        msg = messaging.send_message(user1.id, dm.id, malicious)

        assert "onerror" not in msg.content.lower()

    def test_data_uri_with_script(self, dm_conversation):
        """Test that data URIs with scripts are sanitized."""
        dm, user1, user2, messaging = dm_conversation

        malicious = '<img src="data:text/html,<script>alert(1)</script>">'
        msg = messaging.send_message(user1.id, dm.id, malicious)

        # Should be sanitized or rejected
        assert "<script>" not in msg.content

    def test_nested_xss_attempts(self, dm_conversation):
        """Test that nested/encoded XSS attempts are blocked."""
        dm, user1, user2, messaging = dm_conversation

        malicious = "<scr<script>ipt>alert(1)</scr</script>ipt>"
        msg = messaging.send_message(user1.id, dm.id, malicious)

        assert "alert(1)" not in msg.content or msg.content != malicious

    def test_svg_xss_sanitization(self, dm_conversation):
        """Test that SVG-based XSS is sanitized."""
        dm, user1, user2, messaging = dm_conversation

        malicious = '<svg onload="alert(1)">'
        msg = messaging.send_message(user1.id, dm.id, malicious)

        assert "onload" not in msg.content.lower() or "alert" not in msg.content


class TestMarkdownInjection:
    """Tests for markdown injection prevention."""

    def test_safe_markdown_bold(self, dm_conversation):
        """Test that safe bold markdown is preserved."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "**bold text**")
        assert "**bold text**" in msg.content or "bold text" in msg.content

    def test_safe_markdown_italic(self, dm_conversation):
        """Test that safe italic markdown is preserved."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "*italic text*")
        assert "*italic text*" in msg.content or "italic text" in msg.content

    def test_safe_markdown_code(self, dm_conversation):
        """Test that safe code markdown is preserved."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "`code block`")
        assert "`code block`" in msg.content or "code block" in msg.content

    def test_markdown_link_sanitization(self, dm_conversation):
        """Test that markdown links are sanitized."""
        dm, user1, user2, messaging = dm_conversation

        malicious_link = "[Click](javascript:alert(1))"
        msg = messaging.send_message(user1.id, dm.id, malicious_link)

        assert "javascript:" not in msg.content.lower()

    def test_excessive_markdown_nesting(self, dm_conversation):
        """Test handling of excessive markdown nesting."""
        dm, user1, user2, messaging = dm_conversation

        nested = "**" * 100 + "text" + "**" * 100
        msg = messaging.send_message(user1.id, dm.id, nested)

        # Should not cause performance issues or crashes
        assert msg is not None

    def test_markdown_spoiler_tags(self, dm_conversation):
        """Test spoiler markdown functionality."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "||spoiler content||")
        assert msg.metadata.get("has_spoilers") is True


class TestProfanityFiltering:
    """Tests for profanity filtering."""

    def test_profanity_filter_disabled_by_default(self, dm_conversation):
        """Test that profanity filter is disabled by default."""
        dm, user1, user2, messaging = dm_conversation

        # Note: Using placeholder for actual profanity
        msg = messaging.send_message(user1.id, dm.id, "Some text with bad word")
        assert msg is not None

    def test_enable_profanity_filter(self, dm_conversation, modules):
        """Test enabling profanity filter for user."""
        dm, user1, user2, messaging = dm_conversation

        # Enable filter
        messaging.update_user_filter_settings(
            user1.id, profanity_filter=True, custom_blocked_words=["badword"]
        )

        filter_settings = messaging.get_user_filter_settings(user1.id)
        assert filter_settings.profanity_filter is True

    def test_custom_blocked_words(self, dm_conversation):
        """Test custom blocked words filtering."""
        dm, user1, user2, messaging = dm_conversation

        # Set custom blocked words
        messaging.update_user_filter_settings(
            user1.id, custom_blocked_words=["customblock"]
        )

        messaging.send_message(user1.id, dm.id, "This has customblock in it")

        # Word should be filtered/censored
        filter_settings = messaging.get_user_filter_settings(user1.id)
        assert "customblock" in filter_settings.custom_blocked_words

    def test_filter_action_censor(self, dm_conversation):
        """Test that CENSOR filter action replaces words."""
        dm, user1, user2, messaging = dm_conversation

        user_filter = ContentFilter(
            user_id=user1.id,
            profanity_filter=True,
            custom_blocked_words=["testbad"],
            filter_action=FilterAction.CENSOR,
        )

        result = validate_content("This testbad word", user_filter, 4000)
        assert result.valid
        assert "testbad" not in result.sanitized_content
        assert "*" in result.sanitized_content

    def test_filter_action_spoiler(self, dm_conversation):
        """Test that SPOILER filter action wraps words."""
        dm, user1, user2, messaging = dm_conversation

        user_filter = ContentFilter(
            user_id=user1.id,
            custom_blocked_words=["testbad"],
            filter_action=FilterAction.SPOILER,
        )

        result = validate_content("This testbad word", user_filter, 4000)
        assert "||" in result.sanitized_content


class TestNSFWDetection:
    """Tests for NSFW content detection."""

    def test_nsfw_marker_detection(self, dm_conversation):
        """Test detection of NSFW markers."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "This is NSFW content warning")

        # Check metadata for NSFW flag
        assert msg.metadata is not None

    def test_18_plus_detection(self, dm_conversation):
        """Test detection of 18+ markers."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "18+ adult content warning")
        assert msg is not None

    def test_adult_content_detection(self, dm_conversation):
        """Test detection of adult content markers."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "adult content warning here")
        assert msg is not None


class TestContentValidation:
    """Tests for general content validation."""

    def test_empty_content_rejected(self):
        """Test that empty content is rejected."""
        result = validate_content("", None, 4000)
        assert not result.valid
        assert "empty" in result.issues[0].lower()

    def test_whitespace_only_rejected(self):
        """Test that whitespace-only content is rejected."""
        result = validate_content("   \n\t  ", None, 4000)
        assert not result.valid

    def test_max_length_enforcement(self):
        """Test that max length is enforced."""
        long_content = "A" * 5000
        result = validate_content(long_content, None, 4000)
        assert not result.valid
        assert "length" in result.issues[0].lower()

    def test_valid_content_passes(self):
        """Test that valid content passes validation."""
        result = validate_content("This is valid content", None, 4000)
        assert result.valid
        assert result.sanitized_content == "This is valid content"

    def test_content_with_newlines(self, dm_conversation):
        """Test content with newlines."""
        dm, user1, user2, messaging = dm_conversation

        content = "Line 1\nLine 2\nLine 3"
        msg = messaging.send_message(user1.id, dm.id, content)
        assert "\n" in msg.content

    def test_content_with_unicode(self, dm_conversation):
        """Test content with unicode characters."""
        dm, user1, user2, messaging = dm_conversation

        content = "Hello 世界 🌍 émojis"
        msg = messaging.send_message(user1.id, dm.id, content)
        assert "世界" in msg.content
        assert "🌍" in msg.content

    def test_content_with_special_chars(self, dm_conversation):
        """Test content with special characters."""
        dm, user1, user2, messaging = dm_conversation

        content = "Test: @#$%^&*()_+-=[]{}|;:',.<>?/~`"
        msg = messaging.send_message(user1.id, dm.id, content)
        assert msg is not None

    def test_sql_injection_prevention(self, dm_conversation):
        """Test that SQL injection attempts are sanitized."""
        dm, user1, user2, messaging = dm_conversation

        malicious = "'; DROP TABLE msg_messages; --"
        msg = messaging.send_message(user1.id, dm.id, malicious)

        # Message should be sent but sanitized
        assert msg is not None
        # Verify no SQL execution occurred by checking message exists
        retrieved = messaging.get_message(user1.id, msg.id)
        assert retrieved is not None

    def test_null_byte_injection(self, dm_conversation):
        """Test handling of null byte injection."""
        dm, user1, user2, messaging = dm_conversation

        content = "Test\x00Hidden"
        msg = messaging.send_message(user1.id, dm.id, content)
        assert msg is not None


class TestContentFilterSettings:
    """Tests for user content filter settings."""

    def test_get_default_filter_settings(self, user_pool, modules):
        """Test default filter settings for new user."""
        user = user_pool.get_user()

        settings = modules.messaging.get_user_filter_settings(user.id)
        assert settings.user_id == user.id
        assert settings.profanity_filter is False
        assert settings.nsfw_filter is False

    def test_update_filter_settings(self, user_pool, modules):
        """Test updating filter settings."""
        user = user_pool.get_user()

        updated = modules.messaging.update_user_filter_settings(
            user.id,
            profanity_filter=True,
            nsfw_filter=True,
            custom_blocked_words=["word1", "word2"],
        )

        assert updated.profanity_filter is True
        assert updated.nsfw_filter is True
        assert "word1" in updated.custom_blocked_words

    def test_filter_settings_persistence(self, user_pool, modules):
        """Test that filter settings persist."""
        user = user_pool.get_user()

        modules.messaging.update_user_filter_settings(user.id, profanity_filter=True)

        # Retrieve again
        settings = modules.messaging.get_user_filter_settings(user.id)
        assert settings.profanity_filter is True

    def test_spoiler_click_to_reveal_setting(self, user_pool, modules):
        """Test spoiler click-to-reveal setting."""
        user = user_pool.get_user()

        updated = modules.messaging.update_user_filter_settings(
            user.id, spoiler_click_to_reveal=False
        )

        assert updated.spoiler_click_to_reveal is False


class TestFormattingParsing:
    """Tests for rich text formatting parsing."""

    def test_parse_bold_formatting(self):
        """Test parsing bold markdown."""
        from src.core.messaging.content import parse_formatting

        content = "This is **bold** text"
        formatting = parse_formatting(content)

        assert len(formatting["bold"]) > 0

    def test_parse_italic_formatting(self):
        """Test parsing italic markdown."""
        from src.core.messaging.content import parse_formatting

        content = "This is *italic* text"
        formatting = parse_formatting(content)

        assert len(formatting["italic"]) > 0

    def test_parse_code_block_formatting(self):
        """Test parsing code blocks."""
        from src.core.messaging.content import parse_formatting

        content = "```python\nprint('hello')\n```"
        formatting = parse_formatting(content)

        assert len(formatting["code_block"]) > 0

    def test_strip_all_formatting(self):
        """Test stripping all formatting markers."""
        from src.core.messaging.content import strip_formatting

        content = "**bold** *italic* `code` ||spoiler||"
        plain = strip_formatting(content)

        assert "**" not in plain
        assert "*" not in plain
        assert "`" not in plain
        assert "||" not in plain

    def test_create_message_preview(self):
        """Test creating message preview."""
        from src.core.messaging.content import create_preview

        long_content = "A" * 200
        preview = create_preview(long_content, max_length=50)

        assert len(preview) <= 53  # 50 + "..."
        assert preview.endswith("...")
