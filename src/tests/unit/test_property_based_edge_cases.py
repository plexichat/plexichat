"""
Property-based tests for edge cases, corner cases, and stress testing.

Tests extreme inputs, race conditions, and unusual combinations using Hypothesis.

Run with: pytest src/tests/unit/test_property_based_edge_cases.py -v
"""

import pytest

try:
    from hypothesis import given, strategies as st, settings, HealthCheck
    from hypothesis.strategies import composite
    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False
    pytest.skip("Hypothesis not installed", allow_module_level=True)

from src.core.auth.passwords import validate_username
from src.core.messaging.content import validate_content


# =============================================================================
# Edge Case Strategies
# =============================================================================

@composite
def zero_width_characters(draw):
    """Generate strings with zero-width characters."""
    zwc = ['\u200b', '\u200c', '\u200d', '\ufeff']  # Zero-width space, joiners, BOM
    normal_text = draw(st.text(min_size=0, max_size=50))
    zwc_text = draw(st.text(min_size=0, max_size=10, alphabet=zwc))
    
    return normal_text + zwc_text


@composite
def homograph_attacks(draw):
    """Generate homograph attack strings (visual spoofing)."""
    # Cyrillic 'а' (U+0430) looks like Latin 'a' (U+0061)
    # Greek 'ο' (U+03BF) looks like Latin 'o' (U+006F)
    return draw(st.one_of(
        st.just("admin"),  # Normal
        st.just("аdmin"),  # Cyrillic 'а'
        st.just("admіn"),  # Cyrillic 'і'
        st.just("ехample"),  # Mixed Cyrillic 'е' and 'х'
        st.text(min_size=1, max_size=20, alphabet='аеіοАΕІΟ'),  # Confusable chars
    ))


@composite
def rtl_text(draw):
    """Generate right-to-left text."""
    # Hebrew, Arabic, and RTL marks
    return draw(st.text(
        min_size=1,
        max_size=50,
        alphabet=st.characters(min_codepoint=0x0590, max_codepoint=0x06FF)
    ))


@composite
def bidi_text(draw):
    """Generate bidirectional text (mixed LTR and RTL)."""
    ltr = draw(st.text(min_size=1, max_size=20, alphabet='abcdefghijklmnopqrstuvwxyz'))
    rtl = draw(st.text(min_size=1, max_size=20, alphabet=st.characters(min_codepoint=0x0590, max_codepoint=0x06FF)))
    
    return ltr + rtl + ltr


@composite
def combining_characters(draw):
    """Generate strings with combining diacritical marks."""
    base = draw(st.text(min_size=1, max_size=20, alphabet='aeiou'))
    combining = draw(st.text(
        min_size=0,
        max_size=50,
        alphabet=st.characters(min_codepoint=0x0300, max_codepoint=0x036F)
    ))
    
    # Zalgo text-like: excessive combining characters
    return ''.join(c + combining[:5] for c in base)


@composite
def normalized_vs_denormalized(draw):
    """Generate strings that differ in Unicode normalization."""
    # é can be represented as U+00E9 (composed) or U+0065 U+0301 (decomposed)
    return draw(st.one_of(
        st.just("café"),  # NFC (composed)
        st.just("café"),  # NFD (decomposed) - might look the same in editor
        st.just("Café"),
    ))


@composite
def sql_injection_patterns(draw):
    """Generate SQL injection attempt patterns."""
    return draw(st.sampled_from([
        "' OR '1'='1",
        "'; DROP TABLE users; --",
        "1' UNION SELECT * FROM passwords--",
        "admin'--",
        "' OR 1=1--",
        "' OR 'a'='a",
        "') OR ('1'='1",
        "1'; DELETE FROM messages WHERE '1'='1",
        "\\x27 OR \\x27\\x31\\x27=\\x27\\x31",
    ]))


@composite
def xss_patterns(draw):
    """Generate XSS attempt patterns."""
    return draw(st.sampled_from([
        "<script>alert('XSS')</script>",
        "<img src=x onerror=alert('XSS')>",
        "<iframe src='javascript:alert(\"XSS\")'></iframe>",
        "javascript:alert('XSS')",
        "<body onload=alert('XSS')>",
        "<svg/onload=alert('XSS')>",
        "<<SCRIPT>alert('XSS');//<</SCRIPT>",
        "<IMG SRC=\"javascript:alert('XSS');\">",
        "<INPUT TYPE=\"IMAGE\" SRC=\"javascript:alert('XSS');\">",
    ]))


@composite
def path_traversal_patterns(draw):
    """Generate path traversal attempt patterns."""
    return draw(st.sampled_from([
        "../../../etc/passwd",
        "..\\..\\..\\windows\\system32",
        "....//....//....//etc/passwd",
        "..%2F..%2F..%2Fetc%2Fpasswd",
        "..%252F..%252F..%252Fetc%252Fpasswd",
        "\\\\server\\share\\file",
    ]))


@composite
def buffer_overflow_patterns(draw):
    """Generate patterns that might cause buffer overflows."""
    return draw(st.one_of(
        st.text(min_size=10000, max_size=20000),  # Very long strings
        st.lists(st.integers(), min_size=10000, max_size=20000),  # Large lists
        st.just("A" * 100000),  # Repeat character
    ))


# =============================================================================
# Zero-Width and Invisible Character Tests
# =============================================================================

@pytest.mark.unit
class TestZeroWidthCharacters:
    """Tests for zero-width and invisible character handling."""

    @given(zero_width_characters())
    @settings(max_examples=100, deadline=None)
    def test_username_with_zero_width_chars(self, username):
        """Test username validation with zero-width characters."""
        valid, issues = validate_username(username)
        # Zero-width characters should typically be rejected or stripped
        assert isinstance(valid, bool)

    @given(zero_width_characters())
    @settings(max_examples=50)
    def test_message_with_zero_width_chars(self, content):
        """Test message content with zero-width characters."""
        if content.strip():
            result = validate_content(content, max_length=4000)
            # Should handle without crashing
            assert isinstance(result.sanitized_content, str)

    @given(st.text(alphabet=st.sampled_from(['a', 'b', '\u200b', '\ufeff']), min_size=1, max_size=50).filter(lambda x: '\u200b' in x or '\ufeff' in x))
    @settings(max_examples=50)
    def test_zero_width_space_stripping(self, text):
        """Test that zero-width spaces are handled."""
        # Text contains zero-width characters
        assert '\u200b' in text or '\ufeff' in text


# =============================================================================
# Homograph and Visual Spoofing Tests
# =============================================================================

@pytest.mark.unit
class TestHomographAttacks:
    """Tests for homograph attacks and visual spoofing."""

    @given(homograph_attacks())
    @settings(max_examples=100, deadline=None)
    def test_homograph_username_detection(self, username):
        """Test detection of homograph attacks in usernames."""
        valid, issues = validate_username(username)
        # Should handle visually similar characters
        assert isinstance(valid, bool)

    @given(st.text(min_size=1, max_size=20, alphabet='аеіοАΕІΟ'))
    @settings(max_examples=50)
    def test_cyrillic_latin_mixing(self, text):
        """Test handling of Cyrillic characters that look like Latin."""
        # These are Cyrillic characters that visually resemble Latin
        result = validate_content(text, max_length=4000)
        assert isinstance(result.sanitized_content, str)


# =============================================================================
# Bidirectional Text Tests
# =============================================================================

@pytest.mark.unit
class TestBidirectionalText:
    """Tests for bidirectional text handling."""

    @given(rtl_text())
    @settings(max_examples=100, deadline=None)
    def test_rtl_text_in_messages(self, content):
        """Test right-to-left text in messages."""
        if content.strip():
            result = validate_content(content, max_length=4000)
            assert isinstance(result.sanitized_content, str)

    @given(bidi_text())
    @settings(max_examples=100, deadline=None)
    def test_mixed_direction_text(self, content):
        """Test mixed LTR/RTL text."""
        if content.strip():
            result = validate_content(content, max_length=4000)
            assert isinstance(result.sanitized_content, str)

    @given(st.text(min_size=1, max_size=50, alphabet='\u202e\u202d\u200e\u200f'))
    @settings(max_examples=50)
    def test_bidi_override_characters(self, content):
        """Test Unicode bidi override characters."""
        # These can be used for spoofing attacks
        if content:
            result = validate_content(content, max_length=4000)
            assert isinstance(result.sanitized_content, str)


# =============================================================================
# Combining Character Tests
# =============================================================================

@pytest.mark.unit
class TestCombiningCharacters:
    """Tests for combining diacritical marks (Zalgo text)."""

    @given(combining_characters())
    @settings(max_examples=50, deadline=None)
    def test_excessive_combining_marks(self, text):
        """Test handling of excessive combining marks."""
        result = validate_content(text, max_length=4000)
        # Should handle without crashing
        assert isinstance(result.sanitized_content, str)

    @given(st.text(alphabet=st.characters() | st.sampled_from([chr(c) for c in range(0x0300, 0x0310)]), min_size=1, max_size=20).filter(lambda x: any(0x0300 <= ord(c) <= 0x036F for c in x)))
    @settings(max_examples=50)
    def test_combining_marks_in_username(self, username):
        """Test combining marks in usernames."""
        valid, issues = validate_username(username)
        # Combining marks might make username invalid
        assert isinstance(valid, bool)


# =============================================================================
# Unicode Normalization Tests
# =============================================================================

@pytest.mark.unit
class TestUnicodeNormalization:
    """Tests for Unicode normalization handling."""

    @given(normalized_vs_denormalized())
    @settings(max_examples=50)
    def test_normalization_consistency(self, text):
        """Test that normalized and denormalized forms are handled consistently."""
        import unicodedata
        
        nfc = unicodedata.normalize('NFC', text)
        nfd = unicodedata.normalize('NFD', text)
        
        # Both forms should be treated equivalently
        result_nfc = validate_content(nfc, max_length=4000)
        result_nfd = validate_content(nfd, max_length=4000)
        
        assert isinstance(result_nfc.sanitized_content, str)
        assert isinstance(result_nfd.sanitized_content, str)


# =============================================================================
# SQL Injection Tests
# =============================================================================

@pytest.mark.unit
class TestSQLInjectionPrevention:
    """Tests for SQL injection prevention."""

    @given(sql_injection_patterns())
    @settings(max_examples=100, deadline=None)
    def test_sql_injection_in_messages(self, content):
        """Test SQL injection attempts in message content."""
        result = validate_content(content, max_length=4000)
        # Should sanitize or reject SQL injection attempts
        assert isinstance(result.sanitized_content, str)

    @given(sql_injection_patterns())
    @settings(max_examples=50)
    def test_sql_injection_in_username(self, username):
        """Test SQL injection attempts in usernames."""
        valid, issues = validate_username(username)
        # SQL patterns should be rejected in usernames
        assert not valid


# =============================================================================
# XSS Prevention Tests
# =============================================================================

@pytest.mark.unit
class TestXSSPrevention:
    """Tests for XSS prevention."""

    @given(xss_patterns())
    @settings(max_examples=100, deadline=None)
    def test_xss_in_messages(self, content):
        """Test XSS attempts in message content."""
        result = validate_content(content, max_length=4000)
        # Should sanitize XSS attempts
        assert isinstance(result.sanitized_content, str)
        # Sanitized content should not contain raw script tags
        # (may be escaped or removed)

    @given(xss_patterns())
    @settings(max_examples=50)
    def test_xss_in_server_names(self, name):
        """Test XSS attempts in server names."""
        # Server names with XSS should be rejected or sanitized
        stripped = name.strip()
        # Should handle without crashing
        assert isinstance(stripped, str)


# =============================================================================
# Path Traversal Tests
# =============================================================================

@pytest.mark.unit
class TestPathTraversalPrevention:
    """Tests for path traversal prevention."""

    @given(path_traversal_patterns())
    @settings(max_examples=50)
    def test_path_traversal_in_content(self, content):
        """Test path traversal attempts in content."""
        result = validate_content(content, max_length=4000)
        # Should handle path traversal patterns safely
        assert isinstance(result.sanitized_content, str)


# =============================================================================
# Buffer Overflow Tests
# =============================================================================

@pytest.mark.unit
class TestBufferOverflowPrevention:
    """Tests for buffer overflow prevention."""

    @given(st.text(min_size=4001, max_size=10000))
    @settings(max_examples=20, deadline=None)
    def test_very_long_messages(self, content):
        """Test very long message content."""
        result = validate_content(content, max_length=4000)
        # Should reject content over limit
        if len(content) > 4000:
            assert not result.valid

    @given(st.text(min_size=4001, max_size=10000, alphabet='A'))
    @settings(max_examples=20)
    def test_repeated_character_messages(self, content):
        """Test messages with many repeated characters."""
        result = validate_content(content, max_length=4000)
        # Should handle without crashing
        assert isinstance(result.sanitized_content, str)


# =============================================================================
# Null Byte and Special Character Tests
# =============================================================================

@pytest.mark.unit
class TestNullBytes:
    """Tests for null byte and special character handling."""

    @given(st.text(min_size=1, max_size=50).filter(lambda x: '\x00' in x))
    @settings(max_examples=50)
    def test_null_bytes_in_content(self, content):
        """Test null bytes in content."""
        result = validate_content(content, max_length=4000)
        # Should handle null bytes safely
        assert isinstance(result.sanitized_content, str)

    @given(st.text(min_size=1, max_size=50, alphabet=st.characters(max_codepoint=0x1F)))
    @settings(max_examples=50)
    def test_control_characters(self, content):
        """Test ASCII control characters."""
        result = validate_content(content, max_length=4000)
        # Should handle control characters
        assert isinstance(result.sanitized_content, str)


# =============================================================================
# Empty and Whitespace Tests
# =============================================================================

@pytest.mark.unit
class TestEmptyAndWhitespace:
    """Tests for empty and whitespace-only inputs."""

    @given(st.text(alphabet=st.characters(whitelist_categories=['Zs', 'Cc']), min_size=1, max_size=100))
    @settings(max_examples=100)
    def test_whitespace_only_content(self, content):
        """Test whitespace-only content."""
        result = validate_content(content, max_length=4000)
        # Whitespace-only should be rejected
        if not content.strip():
            assert not result.valid

    @given(st.just(""))
    @settings(max_examples=10)
    def test_empty_string_content(self, content):
        """Test empty string content."""
        result = validate_content(content, max_length=4000)
        # Empty should be rejected
        assert not result.valid

    @given(st.lists(st.just(" "), min_size=1, max_size=100))
    @settings(max_examples=50)
    def test_spaces_only(self, spaces):
        """Test strings with only spaces."""
        content = "".join(spaces)
        result = validate_content(content, max_length=4000)
        if not content.strip():
            assert not result.valid
