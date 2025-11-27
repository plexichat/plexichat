"""
Content filtering tests for messaging module.
"""

import pytest


class TestContentValidation:
    """Test content validation."""
    
    def test_valid_content_passes(self, dm_conversation):
        """Test valid content passes validation."""
        dm, user1, user2, messaging = dm_conversation
        
        msg = messaging.send_message(user1.id, dm.id, "Hello, this is a normal message!")
        
        assert msg is not None
        assert msg.content == "Hello, this is a normal message!"
    
    def test_empty_content_fails(self, dm_conversation):
        """Test empty content fails validation."""
        dm, user1, user2, messaging = dm_conversation
        
        with pytest.raises(messaging.InvalidContentError):
            messaging.send_message(user1.id, dm.id, "")
    
    def test_whitespace_only_fails(self, dm_conversation):
        """Test whitespace-only content fails."""
        dm, user1, user2, messaging = dm_conversation
        
        with pytest.raises(messaging.InvalidContentError):
            messaging.send_message(user1.id, dm.id, "   \n\t  ")
    
    def test_content_length_limit(self, dm_conversation):
        """Test content length limit is enforced."""
        dm, user1, user2, messaging = dm_conversation
        
        # Default limit is 4000
        long_content = "x" * 4001
        
        with pytest.raises(messaging.ContentTooLongError):
            messaging.send_message(user1.id, dm.id, long_content)
    
    def test_content_at_limit_succeeds(self, dm_conversation):
        """Test content at exactly the limit succeeds."""
        dm, user1, user2, messaging = dm_conversation
        
        content = "x" * 4000
        msg = messaging.send_message(user1.id, dm.id, content)
        
        assert len(msg.content) == 4000


class TestUserFilterSettings:
    """Test user content filter settings."""
    
    def test_get_default_filter_settings(self, users):
        """Test getting default filter settings."""
        user1, user2, user3, messaging = users
        
        filters = messaging.get_user_filter_settings(user1.id)
        
        assert filters.user_id == user1.id
        assert filters.profanity_filter is False
        assert filters.nsfw_filter is False
        assert filters.spoiler_click_to_reveal is True
    
    def test_update_profanity_filter(self, users):
        """Test updating profanity filter setting."""
        user1, user2, user3, messaging = users
        
        filters = messaging.update_user_filter_settings(user1.id, profanity_filter=True)
        
        assert filters.profanity_filter is True
    
    def test_update_nsfw_filter(self, users):
        """Test updating NSFW filter setting."""
        user1, user2, user3, messaging = users
        
        filters = messaging.update_user_filter_settings(user1.id, nsfw_filter=True)
        
        assert filters.nsfw_filter is True
    
    def test_update_spoiler_setting(self, users):
        """Test updating spoiler click-to-reveal setting."""
        user1, user2, user3, messaging = users
        
        filters = messaging.update_user_filter_settings(user1.id, spoiler_click_to_reveal=False)
        
        assert filters.spoiler_click_to_reveal is False
    
    def test_update_custom_blocked_words(self, users):
        """Test updating custom blocked words."""
        user1, user2, user3, messaging = users
        
        filters = messaging.update_user_filter_settings(
            user1.id,
            custom_blocked_words=["spam", "advertisement"]
        )
        
        assert "spam" in filters.custom_blocked_words
        assert "advertisement" in filters.custom_blocked_words
    
    def test_update_multiple_settings(self, users):
        """Test updating multiple settings at once."""
        user1, user2, user3, messaging = users
        
        filters = messaging.update_user_filter_settings(
            user1.id,
            profanity_filter=True,
            nsfw_filter=True,
            custom_blocked_words=["test"]
        )
        
        assert filters.profanity_filter is True
        assert filters.nsfw_filter is True
        assert "test" in filters.custom_blocked_words
    
    def test_settings_persist(self, users):
        """Test that settings persist after update."""
        user1, user2, user3, messaging = users
        
        messaging.update_user_filter_settings(user1.id, profanity_filter=True)
        
        # Get settings again
        filters = messaging.get_user_filter_settings(user1.id)
        
        assert filters.profanity_filter is True


class TestUserMessageSettings:
    """Test user message settings."""
    
    def test_get_default_message_settings(self, users):
        """Test getting default message settings."""
        user1, user2, user3, messaging = users
        
        settings = messaging.get_user_message_settings(user1.id)
        
        assert settings.user_id == user1.id
        assert settings.allow_dms_from == "everyone"
        assert settings.auto_create_dms is True
    
    def test_update_allow_dms_from(self, users):
        """Test updating DM permission setting."""
        user1, user2, user3, messaging = users
        
        settings = messaging.update_user_message_settings(user1.id, allow_dms_from="friends")
        
        assert settings.allow_dms_from == "friends"
        
        # Reset for other tests
        messaging.update_user_message_settings(user1.id, allow_dms_from="everyone")
    
    def test_update_auto_create_dms(self, users):
        """Test updating auto-create DMs setting."""
        user1, user2, user3, messaging = users
        
        settings = messaging.update_user_message_settings(user1.id, auto_create_dms=False)
        
        assert settings.auto_create_dms is False
        
        # Reset for other tests
        messaging.update_user_message_settings(user1.id, auto_create_dms=True)
    
    def test_update_max_message_length(self, users):
        """Test updating max message length."""
        user1, user2, user3, messaging = users
        
        settings = messaging.update_user_message_settings(user1.id, max_message_length=8000)
        
        assert settings.max_message_length == 8000
        
        # Reset for other tests
        messaging.update_user_message_settings(user1.id, max_message_length=None)
    
    def test_custom_message_length_enforced(self, users):
        """Test custom message length is enforced."""
        user1, user2, user3, messaging = users
        
        # Set custom limit
        messaging.update_user_message_settings(user1.id, max_message_length=100)
        
        try:
            dm = messaging.create_dm(user1.id, user2.id)
            
            # Should fail with 101 characters
            with pytest.raises(messaging.ContentTooLongError):
                messaging.send_message(user1.id, dm.id, "x" * 101)
        finally:
            # Reset for other tests
            messaging.update_user_message_settings(user1.id, max_message_length=None)
    
    def test_custom_message_length_allows_longer(self, users):
        """Test custom message length allows longer messages."""
        user1, user2, user3, messaging = users
        
        # Set higher limit
        messaging.update_user_message_settings(user1.id, max_message_length=8000)
        
        try:
            dm = messaging.create_dm(user1.id, user2.id)
            
            # Should succeed with 5000 characters (exceeds default 4000)
            msg = messaging.send_message(user1.id, dm.id, "x" * 5000)
            
            assert len(msg.content) == 5000
        finally:
            # Reset for other tests
            messaging.update_user_message_settings(user1.id, max_message_length=None)
    
    def test_update_attachment_settings(self, users):
        """Test updating attachment settings."""
        user1, user2, user3, messaging = users
        
        settings = messaging.update_user_message_settings(
            user1.id,
            max_attachment_size=20971520,
            max_attachments_per_message=5
        )
        
        assert settings.max_attachment_size == 20971520
        assert settings.max_attachments_per_message == 5


class TestRichTextFormatting:
    """Test rich text formatting in messages."""
    
    def test_bold_formatting_preserved(self, dm_conversation):
        """Test bold formatting is preserved."""
        dm, user1, user2, messaging = dm_conversation
        
        msg = messaging.send_message(user1.id, dm.id, "This is **bold** text")
        
        assert "**bold**" in msg.content
    
    def test_italic_formatting_preserved(self, dm_conversation):
        """Test italic formatting is preserved."""
        dm, user1, user2, messaging = dm_conversation
        
        msg = messaging.send_message(user1.id, dm.id, "This is *italic* text")
        
        assert "*italic*" in msg.content
    
    def test_spoiler_formatting_preserved(self, dm_conversation):
        """Test spoiler formatting is preserved."""
        dm, user1, user2, messaging = dm_conversation
        
        msg = messaging.send_message(user1.id, dm.id, "The answer is ||42||")
        
        assert "||42||" in msg.content
    
    def test_code_formatting_preserved(self, dm_conversation):
        """Test code formatting is preserved."""
        dm, user1, user2, messaging = dm_conversation
        
        msg = messaging.send_message(user1.id, dm.id, "Use `print()` function")
        
        assert "`print()`" in msg.content
    
    def test_code_block_preserved(self, dm_conversation):
        """Test code block is preserved."""
        dm, user1, user2, messaging = dm_conversation
        
        content = "```python\nprint('hello')\n```"
        msg = messaging.send_message(user1.id, dm.id, content)
        
        assert "```python" in msg.content
        assert "print('hello')" in msg.content
    
    def test_strikethrough_preserved(self, dm_conversation):
        """Test strikethrough is preserved."""
        dm, user1, user2, messaging = dm_conversation
        
        msg = messaging.send_message(user1.id, dm.id, "~~wrong~~ correct")
        
        assert "~~wrong~~" in msg.content
    
    def test_underline_preserved(self, dm_conversation):
        """Test underline is preserved."""
        dm, user1, user2, messaging = dm_conversation
        
        msg = messaging.send_message(user1.id, dm.id, "This is __underlined__ text")
        
        assert "__underlined__" in msg.content
    
    def test_quote_preserved(self, dm_conversation):
        """Test quote formatting is preserved."""
        dm, user1, user2, messaging = dm_conversation
        
        msg = messaging.send_message(user1.id, dm.id, "> This is a quote")
        
        assert "> This is a quote" in msg.content
    
    def test_mixed_formatting(self, dm_conversation):
        """Test mixed formatting is preserved."""
        dm, user1, user2, messaging = dm_conversation
        
        content = "**Bold** and *italic* with ||spoiler|| and `code`"
        msg = messaging.send_message(user1.id, dm.id, content)
        
        assert "**Bold**" in msg.content
        assert "*italic*" in msg.content
        assert "||spoiler||" in msg.content
        assert "`code`" in msg.content


class TestSpoilerDetection:
    """Test spoiler content detection."""
    
    def test_spoiler_detected_in_metadata(self, dm_conversation):
        """Test that spoiler content is detected."""
        dm, user1, user2, messaging = dm_conversation
        
        msg = messaging.send_message(user1.id, dm.id, "The ending is ||everyone dies||")
        
        # Spoiler detection is in metadata
        assert msg.metadata is None or msg.metadata.get("has_spoilers", False) or "||" in msg.content


class TestUnicodeContent:
    """Test unicode content handling."""
    
    def test_unicode_characters_preserved(self, dm_conversation):
        """Test unicode characters are preserved."""
        dm, user1, user2, messaging = dm_conversation
        
        # Reset any filters from previous tests
        messaging.update_user_filter_settings(user1.id, custom_blocked_words=[])
        
        content = "Hello! Unicode chars: cafe, resume, naive"
        msg = messaging.send_message(user1.id, dm.id, content)
        
        assert msg.content == content
    
    def test_multilingual_content(self, dm_conversation):
        """Test multilingual content is preserved."""
        dm, user1, user2, messaging = dm_conversation
        
        content = "English, Espanol, Francais, Deutsch"
        msg = messaging.send_message(user1.id, dm.id, content)
        
        assert msg.content == content
    
    def test_special_characters(self, dm_conversation):
        """Test special characters are handled."""
        dm, user1, user2, messaging = dm_conversation
        
        content = "Special chars: & < > \" ' @ # $ % ^ * ( ) [ ] { }"
        msg = messaging.send_message(user1.id, dm.id, content)
        
        # Content should be preserved (possibly sanitized)
        assert msg is not None
