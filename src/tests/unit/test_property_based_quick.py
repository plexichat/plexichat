"""
Quick property-based tests for CI/CD and rapid validation.

A subset of critical tests with reduced example counts for fast feedback.

Run with: pytest src/tests/unit/test_property_based_quick.py -v
"""

import pytest

try:
    from hypothesis import given, strategies as st, settings, example
    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False
    pytest.skip("Hypothesis not installed", allow_module_level=True)

from src.core.auth.passwords import validate_username, validate_email, validate_password
from src.core.messaging.content import validate_content


# Quick test settings: fewer examples, but covering critical paths
QUICK_SETTINGS = settings(max_examples=50, deadline=1000)


@pytest.mark.unit
class TestQuickAuthValidation:
    """Quick auth validation tests."""

    @given(st.text(min_size=0, max_size=50))
    @QUICK_SETTINGS
    @example("")
    @example("ab")  # Too short
    @example("a" * 50)  # Too long
    def test_username_length_boundaries(self, username):
        """Test username length validation (quick)."""
        valid, issues = validate_username(username)
        
        if len(username) < 3 or len(username) > 32:
            assert not valid

    @given(st.text(min_size=0, max_size=100))
    @QUICK_SETTINGS
    @example("test@example.com")
    @example("invalid")
    @example("@example.com")
    def test_email_basic_validation(self, email):
        """Test email basic validation (quick)."""
        result = validate_email(email)
        
        if '@' not in email or '.' not in email.split('@')[-1] if '@' in email else False:
            assert not result

    @given(st.text(min_size=0, max_size=150))
    @QUICK_SETTINGS
    @example("ValidPass123!")
    @example("short")
    @example("a" * 200)
    def test_password_length(self, password):
        """Test password length validation (quick)."""
        result = validate_password(password)
        
        if len(password) < 12 or len(password) > 128:
            assert not result.valid


@pytest.mark.unit
class TestQuickMessageValidation:
    """Quick message validation tests."""

    @given(st.text(min_size=0, max_size=5000))
    @QUICK_SETTINGS
    @example("")
    @example("Valid message")
    @example("a" * 5000)
    def test_message_length(self, content):
        """Test message length validation (quick)."""
        result = validate_content(content, max_length=4000)
        
        if not content.strip():
            assert not result.valid
        
        if len(content) > 4000:
            assert not result.valid

    @given(st.text(min_size=1, max_size=100, alphabet='<>'))
    @QUICK_SETTINGS
    def test_html_sanitization(self, content):
        """Test HTML tag handling (quick)."""
        result = validate_content(content, max_length=4000)
        assert isinstance(result.sanitized_content, str)


@pytest.mark.unit
class TestQuickSecurityValidation:
    """Quick security validation tests."""

    @given(st.sampled_from([
        "<script>alert('XSS')</script>",
        "'; DROP TABLE users; --",
        "../../../etc/passwd",
    ]))
    @QUICK_SETTINGS
    def test_common_attacks(self, attack):
        """Test common attack pattern handling (quick)."""
        result = validate_content(attack, max_length=4000)
        # Should not crash and should sanitize
        assert isinstance(result.sanitized_content, str)


@pytest.mark.unit
class TestQuickUnicodeHandling:
    """Quick Unicode handling tests."""

    @given(st.text(
        min_size=1,
        max_size=50,
        alphabet=st.characters(min_codepoint=0x1F600, max_codepoint=0x1F64F)
    ))
    @QUICK_SETTINGS
    @example("🎉")
    def test_emoji_handling(self, content):
        """Test emoji handling (quick)."""
        if content.strip():
            result = validate_content(content, max_length=4000)
            assert isinstance(result.sanitized_content, str)

    @given(st.text(
        min_size=1,
        max_size=50,
        alphabet=st.characters(min_codepoint=0x4E00, max_codepoint=0x9FFF)
    ))
    @QUICK_SETTINGS
    def test_cjk_handling(self, content):
        """Test CJK character handling (quick)."""
        if content.strip():
            result = validate_content(content, max_length=4000)
            assert isinstance(result.sanitized_content, str)


@pytest.mark.unit
class TestQuickBoundaryConditions:
    """Quick boundary condition tests."""

    @given(st.integers(min_value=0, max_value=5000))
    @QUICK_SETTINGS
    @example(0)
    @example(1)
    @example(4000)
    @example(4001)
    def test_message_boundaries(self, length):
        """Test message length boundaries (quick)."""
        content = "a" * length
        result = validate_content(content, max_length=4000)
        
        if length == 0:
            assert not result.valid
        elif 0 < length <= 4000:
            assert result.valid
        else:
            assert not result.valid

    @given(st.integers(min_value=0, max_value=200))
    @QUICK_SETTINGS
    @example(11)
    @example(12)
    @example(128)
    @example(129)
    def test_password_boundaries(self, length):
        """Test password length boundaries (quick)."""
        password = "Aa1!" + "a" * max(0, length - 4)
        result = validate_password(password)
        
        if length < 12:
            assert not result.valid
        elif length > 128:
            assert not result.valid
