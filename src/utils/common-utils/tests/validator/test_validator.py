import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from utils.validator import Validator, ValidationResult


class TestValidationResult:
    """Tests for ValidationResult dataclass."""

    def test_validation_result_valid(self):
        result = ValidationResult(
            is_valid=True, sanitized_value="clean", error_message=None
        )
        assert result.is_valid is True
        assert result.sanitized_value == "clean"
        assert result.error_message is None

    def test_validation_result_invalid(self):
        result = ValidationResult(
            is_valid=False, sanitized_value=None, error_message="error"
        )
        assert result.is_valid is False
        assert result.sanitized_value is None
        assert result.error_message == "error"


class TestBasicValidation:
    """Tests for basic validation functionality."""

    def test_clean_text(self):
        validator = Validator()
        result = validator.validate("Hello World")
        assert result.is_valid
        assert result.sanitized_value == "Hello World"

    def test_empty_string(self):
        validator = Validator()
        result = validator.validate("")
        assert result.is_valid
        assert result.sanitized_value == ""

    def test_none_text(self):
        validator = Validator()
        result = validator.validate(None)
        assert result.is_valid
        assert result.sanitized_value is None

    def test_whitespace_only(self):
        validator = Validator()
        result = validator.validate("   ")
        assert result.is_valid
        assert result.sanitized_value == "   "

    def test_numeric_string(self):
        validator = Validator()
        result = validator.validate("12345")
        assert result.is_valid
        assert result.sanitized_value == "12345"


class TestSQLInjectionPrevention:
    """Security tests for SQL injection prevention."""

    def test_union_select_blocked(self):
        validator = Validator()
        result = validator.validate(
            "SELECT * FROM users UNION SELECT password FROM admin"
        )
        assert not result.is_valid
        assert result.error_message is not None
        assert "blocked pattern" in result.error_message.lower()

    def test_drop_table_blocked(self):
        validator = Validator(blocklist_patterns=[r"DROP\s+TABLE"])
        result = validator.validate("DROP TABLE users")
        assert not result.is_valid
        assert result.error_message is not None
        assert "blocked pattern" in result.error_message

    def test_insert_into_blocked(self):
        validator = Validator()
        result = validator.validate("INSERT INTO users VALUES ('hacker', 'pass')")
        assert not result.is_valid

    def test_update_set_blocked(self):
        validator = Validator()
        result = validator.validate("UPDATE users SET password='hacked' WHERE id=1")
        assert not result.is_valid

    def test_delete_from_blocked(self):
        validator = Validator()
        result = validator.validate("DELETE FROM users WHERE 1=1")
        assert not result.is_valid

    def test_or_equals_injection(self):
        validator = Validator()
        result = validator.validate("admin' OR 1=1--")
        assert not result.is_valid

    def test_sql_comment_blocked(self):
        validator = Validator()
        result = validator.validate("user'--")
        assert not result.is_valid

    def test_semicolon_injection(self):
        validator = Validator()
        result = validator.validate("user'; DROP TABLE users;")
        assert not result.is_valid

    def test_case_insensitive_sql_detection(self):
        validator = Validator()
        result = validator.validate("SeLeCt * FrOm users")
        assert not result.is_valid

    def test_select_from_with_newlines(self):
        validator = Validator()
        result = validator.validate("SELECT\n*\nFROM\nusers")
        assert not result.is_valid


class TestXSSPrevention:
    """Security tests for XSS (Cross-Site Scripting) prevention."""

    def test_script_tag_blocked(self):
        validator = Validator(blocklist_patterns=[r"<script>"])
        result = validator.validate("<script>alert(1)</script>")
        assert not result.is_valid
        assert result.error_message is not None
        assert "<script>" in result.error_message

    def test_script_with_attributes_blocked(self):
        validator = Validator()
        result = validator.validate("<script src='evil.js'></script>")
        assert not result.is_valid

    def test_javascript_protocol_blocked(self):
        validator = Validator()
        result = validator.validate("<a href='javascript:alert(1)'>click</a>")
        assert not result.is_valid

    def test_event_handler_blocked(self):
        validator = Validator()
        result = validator.validate("<img src=x onerror='alert(1)'>")
        assert not result.is_valid

    def test_onclick_blocked(self):
        validator = Validator()
        result = validator.validate("<div onclick='alert(1)'>click</div>")
        assert not result.is_valid

    def test_iframe_blocked(self):
        validator = Validator()
        result = validator.validate("<iframe src='evil.com'></iframe>")
        assert not result.is_valid

    def test_object_tag_blocked(self):
        validator = Validator()
        result = validator.validate("<object data='evil.swf'></object>")
        assert not result.is_valid

    def test_case_insensitive_xss_detection(self):
        validator = Validator()
        result = validator.validate("<ScRiPt>alert(1)</ScRiPt>")
        assert not result.is_valid

    def test_onload_handler_blocked(self):
        validator = Validator()
        result = validator.validate("<body onload='alert(1)'>")
        assert not result.is_valid

    def test_onmouseover_blocked(self):
        validator = Validator()
        result = validator.validate("<img onmouseover='alert(1)'>")
        assert not result.is_valid


class TestHTMLSanitization:
    """Tests for HTML auto-sanitization."""

    def test_html_escape_enabled(self):
        validator = Validator(auto_sanitize_html=True)
        result = validator.validate("<b>Bold Text</b>")
        assert result.is_valid
        assert result.sanitized_value == "&lt;b&gt;Bold Text&lt;/b&gt;"

    def test_html_escape_disabled(self):
        validator = Validator(auto_sanitize_html=False, blocklist_patterns=[])
        result = validator.validate("<b>Bold Text</b>")
        assert result.is_valid
        assert result.sanitized_value == "<b>Bold Text</b>"

    def test_ampersand_escaped(self):
        validator = Validator(auto_sanitize_html=True, blocklist_patterns=[])
        result = validator.validate("A & B")
        assert result.is_valid
        assert result.sanitized_value == "A &amp; B"

    def test_quotes_escaped(self):
        validator = Validator(auto_sanitize_html=True, blocklist_patterns=[])
        result = validator.validate('Say "Hello"')
        assert result.is_valid
        assert result.sanitized_value == "Say &quot;Hello&quot;"

    def test_apostrophe_escaped(self):
        validator = Validator(auto_sanitize_html=True, blocklist_patterns=[])
        result = validator.validate("It's fine")
        assert result.is_valid
        assert result.sanitized_value == "It&#x27;s fine"

    def test_less_than_greater_than_escaped(self):
        validator = Validator(auto_sanitize_html=True, blocklist_patterns=[])
        result = validator.validate("1 < 2 > 0")
        assert result.is_valid
        assert result.sanitized_value == "1 &lt; 2 &gt; 0"


class TestEscapeCharHandling:
    """Tests for escape character handling."""

    def test_escaped_allowed(self):
        validator = Validator(
            allow_escaped=True, escape_char='"', blocklist_patterns=[r"DROP\s+TABLE"]
        )
        text = '"DROP TABLE users"'
        result = validator.validate(text)
        assert result.is_valid
        assert result.sanitized_value == text

    def test_escaped_disallowed(self):
        validator = Validator(allow_escaped=False, blocklist_patterns=[r"DROP\s+TABLE"])
        text = '"DROP TABLE users"'
        result = validator.validate(text)
        assert not result.is_valid

    def test_partial_quote_not_escaped(self):
        validator = Validator(
            allow_escaped=True, escape_char='"', blocklist_patterns=[r"DROP\s+TABLE"]
        )
        text = 'DROP TABLE users"'
        result = validator.validate(text)
        assert not result.is_valid

    def test_quote_at_end_only(self):
        validator = Validator(
            allow_escaped=True, escape_char='"', blocklist_patterns=[r"DROP\s+TABLE"]
        )
        text = '"DROP TABLE users'
        result = validator.validate(text)
        assert not result.is_valid

    def test_custom_escape_char(self):
        validator = Validator(
            allow_escaped=True, escape_char="'", blocklist_patterns=[r"DROP\s+TABLE"]
        )
        text = "'DROP TABLE users'"
        result = validator.validate(text)
        assert result.is_valid

    def test_empty_escape_char(self):
        validator = Validator(
            allow_escaped=True, escape_char="", blocklist_patterns=[r"DROP\s+TABLE"]
        )
        text = "DROP TABLE users"
        result = validator.validate(text)
        assert not result.is_valid


class TestCustomPatterns:
    """Tests for custom blocklist patterns."""

    def test_custom_pattern_single(self):
        validator = Validator(blocklist_patterns=[r"foo"])
        assert not validator.validate("foo bar").is_valid
        assert validator.validate("bar baz").is_valid

    def test_custom_pattern_multiple(self):
        validator = Validator(blocklist_patterns=[r"foo", r"bar"])
        assert not validator.validate("foo").is_valid
        assert not validator.validate("bar").is_valid
        assert validator.validate("baz").is_valid

    def test_add_pattern_method(self):
        validator = Validator(blocklist_patterns=[r"foo"])
        assert validator.validate("bar baz").is_valid

        validator.add_pattern(r"bar")
        assert not validator.validate("bar baz").is_valid

    def test_regex_special_chars(self):
        validator = Validator(blocklist_patterns=[r"\d{3}-\d{2}-\d{4}"])
        result = validator.validate("SSN: 123-45-6789")
        assert not result.is_valid

    def test_complex_regex_pattern(self):
        validator = Validator(blocklist_patterns=[r"(?i)password\s*[:=]\s*\S+"])
        result = validator.validate("password: secret123")
        assert not result.is_valid

    def test_empty_pattern_list(self):
        validator = Validator(blocklist_patterns=[])
        result = validator.validate("anything goes")
        assert result.is_valid

    def test_pattern_with_groups(self):
        validator = Validator(blocklist_patterns=[r"(admin|root|superuser)"])
        assert not validator.validate("login as admin").is_valid
        assert not validator.validate("root access").is_valid
        assert not validator.validate("superuser mode").is_valid


class TestAdvancedSQLInjection:
    """Advanced SQL injection attack vectors."""

    def test_stacked_queries(self):
        validator = Validator()
        result = validator.validate("SELECT id FROM users; DROP TABLE sessions;")
        assert not result.is_valid

    def test_boolean_based_blind_injection(self):
        validator = Validator()
        result = validator.validate("1' OR '1'='1")
        assert not result.is_valid

    def test_time_based_blind_injection(self):
        validator = Validator()
        result = validator.validate("1'; WAITFOR DELAY '00:00:05'--")
        assert not result.is_valid

    def test_union_null_injection(self):
        validator = Validator()
        result = validator.validate("1 UNION SELECT NULL, NULL, NULL")
        assert not result.is_valid

    def test_order_by_injection(self):
        validator = Validator()
        result = validator.validate("SELECT * FROM users ORDER BY 1--")
        assert not result.is_valid

    def test_hex_encoded_string(self):
        validator = Validator()
        result = validator.validate("SELECT * FROM users WHERE name=0x61646d696e")
        assert not result.is_valid


class TestAdvancedXSS:
    """Advanced XSS attack vectors."""

    def test_encoded_javascript(self):
        validator = Validator()
        result = validator.validate(
            "<img src=x onerror='&#97;&#108;&#101;&#114;&#116;(1)'>"
        )
        assert not result.is_valid

    def test_svg_xss(self):
        validator = Validator()
        result = validator.validate("<svg onload='alert(1)'>")
        assert not result.is_valid

    def test_data_uri_javascript(self):
        validator = Validator()
        result = validator.validate(
            "<a href='data:text/html,<script>alert(1)</script>'>click</a>"
        )
        assert not result.is_valid

    def test_form_action_javascript(self):
        validator = Validator()
        result = validator.validate("<form action='javascript:alert(1)'>")
        assert not result.is_valid

    def test_style_attribute_xss(self):
        validator = Validator(blocklist_patterns=[r"(?i)on\w+\s*="])
        result = validator.validate(
            "<div style='background: url(javascript:alert(1))'>"
        )
        assert result.is_valid

        validator2 = Validator(blocklist_patterns=[r"(?i)javascript:"])
        result2 = validator2.validate(
            "<div style='background: url(javascript:alert(1))'>"
        )
        assert not result2.is_valid


class TestConfigurationOptions:
    """Tests for validator configuration options."""

    def test_default_initialization(self):
        validator = Validator()
        assert validator.escape_char == '"'
        assert validator.allow_escaped is True
        assert validator.auto_sanitize_html is True
        assert len(validator.blocklist_patterns) > 0
        assert len(validator.compiled_patterns) > 0

    def test_custom_initialization(self):
        patterns = [r"test"]
        validator = Validator(
            blocklist_patterns=patterns,
            escape_char="'",
            allow_escaped=False,
            auto_sanitize_html=False,
        )
        assert validator.escape_char == "'"
        assert validator.allow_escaped is False
        assert validator.auto_sanitize_html is False
        assert len(validator.compiled_patterns) == 1

    def test_default_patterns_loaded(self):
        validator = Validator()
        sql_patterns_present = any("UNION" in p for p in validator.blocklist_patterns)
        xss_patterns_present = any(
            "script" in p.lower() for p in validator.blocklist_patterns
        )
        assert sql_patterns_present
        assert xss_patterns_present

    def test_compiled_patterns_count(self):
        patterns = [r"foo", r"bar", r"baz"]
        validator = Validator(blocklist_patterns=patterns)
        assert len(validator.compiled_patterns) == len(patterns)


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_very_long_string(self):
        validator = Validator()
        long_text = "A" * 10000
        result = validator.validate(long_text)
        assert result.is_valid

    def test_unicode_characters(self):
        validator = Validator(blocklist_patterns=[])
        result = validator.validate("Hello World")
        assert result.is_valid

    def test_newlines_and_tabs(self):
        validator = Validator(blocklist_patterns=[])
        result = validator.validate("Line1\nLine2\tTab")
        assert result.is_valid

    def test_mixed_attack_vectors(self):
        validator = Validator()
        result = validator.validate("<script>DROP TABLE users</script>")
        assert not result.is_valid

    def test_pattern_at_string_start(self):
        validator = Validator(blocklist_patterns=[r"^DROP"])
        result = validator.validate("DROP TABLE users")
        assert not result.is_valid

        result2 = validator.validate("Don't DROP TABLE users")
        assert result2.is_valid

    def test_pattern_at_string_end(self):
        validator = Validator(blocklist_patterns=[r"--;$"])
        result = validator.validate("SELECT * FROM users--;")
        assert not result.is_valid

    def test_multiple_patterns_match(self):
        validator = Validator()
        result = validator.validate(
            "<script>SELECT * FROM users UNION SELECT password</script>"
        )
        assert not result.is_valid

    def test_whitespace_variations(self):
        validator = Validator()
        result = validator.validate("SELECT\t*\r\nFROM  users")
        assert not result.is_valid

    def test_null_bytes(self):
        validator = Validator(blocklist_patterns=[])
        result = validator.validate("Hello\x00World")
        assert result.is_valid

    def test_escaped_with_html_sanitization(self):
        validator = Validator(
            allow_escaped=True, escape_char='"', auto_sanitize_html=True
        )
        text = '"<script>alert(1)</script>"'
        result = validator.validate(text)
        assert result.is_valid
        assert result.sanitized_value is not None
        assert "&lt;" in result.sanitized_value
        assert "&gt;" in result.sanitized_value
