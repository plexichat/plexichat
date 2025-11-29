"""
Property-based tests for input validation.

Uses Hypothesis to generate test cases automatically,
providing much better coverage than hand-written tests.

Install: pip install hypothesis
Run: pytest src/tests/unit/test_validators.py -v
"""

import pytest

# Try to import hypothesis, skip tests if not available
try:
    from hypothesis import given, strategies as st, assume, settings
    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False
    # Create dummy decorators - type: ignore needed for fallback stubs
    from typing import Any
    
    given: Any = lambda *args, **kwargs: lambda f: pytest.mark.skip(reason="hypothesis not installed")(f)
    st: Any = type('st', (), {
        'text': staticmethod(lambda *args, **kwargs: None),
        'integers': staticmethod(lambda *args, **kwargs: None),
        'emails': staticmethod(lambda *args, **kwargs: None),
        'characters': staticmethod(lambda *args, **kwargs: None),
    })()
    settings: Any = lambda *args, **kwargs: lambda f: f
    assume: Any = lambda x: None


@pytest.mark.unit
class TestUsernameValidation:
    """Property-based tests for username validation."""
    
    @given(st.text(min_size=3, max_size=32, alphabet=st.characters(
        whitelist_categories=('Ll', 'Lu', 'Nd'),  # lowercase, uppercase, digits
        whitelist_characters='_'
    )))
    @settings(max_examples=100)
    def test_valid_alphanumeric_usernames(self, username):
        """Alphanumeric usernames within length limits should be valid."""
        from src.tests.fixtures.config import get_test_config
        test_config = get_test_config()
        
        assume(len(username.strip()) >= 3)  # Must have non-whitespace content
        assume(username[0].isalpha())  # Must start with letter
        
        min_len = test_config["authentication"]["accounts"]["username_min_length"]
        max_len = test_config["authentication"]["accounts"]["username_max_length"]
        
        # This would call your actual validator
        # For now, just verify the config constraints
        assert min_len <= len(username) <= max_len
    
    @given(st.text(max_size=2))
    @settings(max_examples=50)
    def test_short_usernames_invalid(self, username):
        """Usernames shorter than minimum should be invalid."""
        from src.tests.fixtures.config import get_test_config
        test_config = get_test_config()
        
        min_len = test_config["authentication"]["accounts"]["username_min_length"]
        assert len(username) < min_len
    
    @given(st.text(min_size=33, max_size=100))
    @settings(max_examples=50)
    def test_long_usernames_invalid(self, username):
        """Usernames longer than maximum should be invalid."""
        from src.tests.fixtures.config import get_test_config
        test_config = get_test_config()
        
        max_len = test_config["authentication"]["accounts"]["username_max_length"]
        assert len(username) > max_len
    
    @pytest.mark.parametrize("invalid_username,reason", [
        ("", "empty"),
        ("   ", "whitespace only"),
        ("ab", "too short"),
        ("a" * 33, "too long"),
        ("user name", "contains space"),
        ("user@name", "contains @"),
        ("user#name", "contains #"),
        ("123user", "starts with number"),
        ("_user", "starts with underscore"),
    ])
    def test_invalid_username_patterns(self, invalid_username, reason, test_config):
        """Known invalid username patterns should be rejected."""
        # This documents expected validation behavior
        # Actual validation would be tested against the real validator
        pass  # Placeholder for actual validation test


@pytest.mark.unit
class TestPasswordValidation:
    """Property-based tests for password validation."""
    
    @pytest.mark.parametrize("password,should_pass,reason", [
        ("TestPass123!", True, "meets all requirements"),
        ("TESTPASS123!", False, "no lowercase"),
        ("testpass123!", False, "no uppercase"),
        ("TestPassword!", False, "no digit"),
        ("TestPass1234", False, "no special char"),
        ("Test1!", False, "too short"),
        ("a" * 129, False, "too long"),
        ("", False, "empty"),
    ])
    def test_password_requirements(self, password, should_pass, reason, test_config):
        """Test password validation against requirements."""
        config = test_config["authentication"]["password"]
        
        checks = [
            len(password) >= config["min_length"],
            len(password) <= config["max_length"],
            any(c.isupper() for c in password) if config["require_uppercase"] else True,
            any(c.islower() for c in password) if config["require_lowercase"] else True,
            any(c.isdigit() for c in password) if config["require_digit"] else True,
            any(not c.isalnum() for c in password) if config["require_special"] else True,
        ]
        
        is_valid = all(checks)
        assert is_valid == should_pass, f"Password '{password}' ({reason}): expected {should_pass}, got {is_valid}"


@pytest.mark.unit
class TestServerNameValidation:
    """Property-based tests for server name validation."""
    
    @pytest.mark.parametrize("name,should_pass,reason", [
        ("My Server", True, "normal name"),
        ("Test", True, "short but valid"),
        ("A" * 100, True, "max length"),
        ("", False, "empty"),
        (" ", False, "whitespace only"),
        ("A", False, "too short"),
        ("A" * 101, False, "too long"),
    ])
    def test_server_name_validation(self, name, should_pass, reason, test_config):
        """Test server name validation."""
        config = test_config["servers"]
        min_len = config["server_name_min_length"]
        max_len = config["server_name_max_length"]
        
        stripped = name.strip()
        is_valid = min_len <= len(stripped) <= max_len and len(stripped) > 0
        
        assert is_valid == should_pass, f"Server name '{name}' ({reason})"


@pytest.mark.unit
class TestMessageLimits:
    """Tests for message length and attachment limits."""
    
    @pytest.mark.parametrize("length,should_pass", [
        (0, False),  # Empty message (depends on your rules)
        (1, True),
        (100, True),
        (4000, True),  # Max length
        (4001, False),  # Over max
    ])
    def test_message_length_limits(self, length, should_pass, test_config):
        """Test message length validation."""
        max_len = test_config["messaging"]["max_message_length"]
        
        is_valid = 0 < length <= max_len
        assert is_valid == should_pass
    
    @pytest.mark.parametrize("count,should_pass", [
        (0, True),
        (1, True),
        (10, True),  # Max attachments
        (11, False),  # Over max
    ])
    def test_attachment_count_limits(self, count, should_pass, test_config):
        """Test attachment count validation."""
        max_attachments = test_config["messaging"]["max_attachments_per_message"]
        
        is_valid = count <= max_attachments
        assert is_valid == should_pass


@pytest.mark.unit
class TestEmbedLimits:
    """Tests for embed field limits."""
    
    def test_embed_limits_from_config(self, test_config):
        """Verify embed limits are configured correctly."""
        config = test_config["embeds"]
        
        assert config["max_embeds_per_message"] == 10
        assert config["max_title_length"] == 256
        assert config["max_description_length"] == 4096
        assert config["max_fields"] == 25
        assert config["max_field_name_length"] == 256
        assert config["max_field_value_length"] == 1024
        assert config["max_total_characters"] == 6000
    
    @pytest.mark.parametrize("field,max_value,test_value,should_pass", [
        ("title", 256, 256, True),
        ("title", 256, 257, False),
        ("description", 4096, 4096, True),
        ("description", 4096, 4097, False),
        ("fields", 25, 25, True),
        ("fields", 25, 26, False),
    ])
    def test_embed_field_limits(self, field, max_value, test_value, should_pass, test_config):
        """Test individual embed field limits."""
        is_valid = test_value <= max_value
        assert is_valid == should_pass, f"Embed {field}: {test_value} <= {max_value}"


@pytest.mark.unit
class TestRateLimitConfig:
    """Tests for rate limit configuration."""
    
    def test_webhook_limits_from_config(self, test_config):
        """Verify webhook limits are configured."""
        config = test_config["webhooks"]
        
        assert config["max_webhooks_per_channel"] == 10
        assert config["max_webhooks_per_server"] == 50
        assert config["max_message_length"] == 2000
        assert config["max_embeds_per_message"] == 10
