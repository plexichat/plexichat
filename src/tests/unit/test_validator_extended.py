"""Extended targeted tests for the common Validator (blocklist + escape + sanitize)."""

import pytest

from utils.validator import Validator, ValidationResult, validate, add_pattern


# ---------------------------------------------------------------------------
# Blocklist detection (default patterns)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "UNION SELECT password FROM users",
        "select id from users",
        "INSERT INTO users VALUES (1)",
        "UPDATE users SET admin=1",
        "DELETE FROM users",
        "DROP TABLE users",
        "1 OR 1=1",
        "x OR 1 = 1",
        "abc -- comment",
        "SELECT * FROM users;",
        "drop table;",
        "<script>alert(1)</script>",
        "<SCRIPT>alert(1)</SCRIPT>",
        "click javascript:alert(1)",
        "<img onerror=alert(1)>",
        "<iframe src=x></iframe>",
        "<object data=x></object>",
    ],
)
def test_blocked_patterns_rejected(text):
    result = Validator().validate(text)
    assert not result.is_valid
    assert result.sanitized_value is None
    assert "blocked pattern" in (result.error_message or "")


@pytest.mark.parametrize(
    "text",
    [
        "normal chat message",
        "hello world",
        "SELECTion of items",
        "orchestra",
        "DELETE me from the list please",  # not DELETE FROM
        "john@example.com",
        "price: 1=1? no",
        "100%",
        "2 + 2 = 4",
        "unicode: héllo wörld 日本語",
        "line1\nline2\nline3",
        "a,b;c,d",
        "user input with <b>bold</b> intent",
    ],
)
def test_benign_text_accepted(text):
    result = Validator().validate(text)
    assert result.is_valid


# ---------------------------------------------------------------------------
# Escape char handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "escape_char", "expected_valid"),
    [
        # Fully enclosed in the escape char -> allowed when allow_escaped
        ('"DROP TABLE users"', '"', True),
        ("'DROP TABLE users'", "'", True),
        ('"UNION SELECT 1"', '"', True),
        ('"<script>alert(1)</script>"', '"', True),
        # Only one side quoted -> still blocked
        ('DROP TABLE users"', '"', False),
        ('"DROP TABLE users', '"', False),
        ("DROP TABLE users'", "'", False),
        ("'DROP TABLE users", "'", False),
        # Empty escape char -> nothing can be escaped
        ("DROP TABLE users", "", False),
        # Blocked pattern outside the quoted segments -> still blocked
        ('"safe" DROP TABLE "more"', '"', False),
        ("prefix 'quoted' DROP TABLE suffix", "'", False),
    ],
)
def test_escape_semantics(text, escape_char, expected_valid):
    v = Validator(
        allow_escaped=True,
        escape_char=escape_char,
        blocklist_patterns=[
            r"DROP\s+TABLE",
            r"UNION\s+SELECT",
            r"<script.*?>.*?</script.*?>",
        ],
    )
    result = v.validate(text)
    assert result.is_valid is expected_valid, (text, result.error_message)


def test_escape_disabled_blocks_quoted_pattern():
    v = Validator(allow_escaped=False, blocklist_patterns=[r"DROP\s+TABLE"])
    assert not v.validate('"DROP TABLE users"').is_valid


def test_escaped_allowed_returns_original_text():
    v = Validator(
        allow_escaped=True, escape_char='"', blocklist_patterns=[r"DROP\s+TABLE"]
    )
    result = v.validate('"DROP TABLE users"')
    assert result.is_valid
    assert result.sanitized_value == '"DROP TABLE users"'


def test_escaped_script_still_html_sanitized():
    v = Validator(allow_escaped=True, escape_char='"')
    result = v.validate('"<script>alert(1)</script>"')
    assert result.is_valid
    assert result.sanitized_value == '"&lt;script&gt;alert(1)&lt;/script&gt;"'


def test_default_allow_escaped_is_true():
    v = Validator()
    assert v.allow_escaped is True
    assert v.escape_char == '"'
    assert v.auto_sanitize_html is True


# ---------------------------------------------------------------------------
# HTML sanitization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("A & B", "A &amp; B"),
        ("1 < 2 > 0", "1 &lt; 2 &gt; 0"),
        ('Say "Hello"', "Say &quot;Hello&quot;"),
        ("It's fine", "It&#x27;s fine"),
        ("<b>bold</b>", "&lt;b&gt;bold&lt;/b&gt;"),
        ("", ""),
        ("no tags here", "no tags here"),
    ],
)
def test_html_escaped_without_blocked_patterns(raw, expected):
    v = Validator(blocklist_patterns=[])
    result = v.validate(raw)
    assert result.is_valid
    assert result.sanitized_value == expected


def test_auto_sanitize_disabled():
    v = Validator(auto_sanitize_html=False, blocklist_patterns=[])
    result = v.validate("<b>x</b>")
    assert result.is_valid
    assert result.sanitized_value == "<b>x</b>"


def test_sanitized_with_blocked_unescaped_returns_none():
    v = Validator()
    result = v.validate("<script>alert(1)</script>")
    assert not result.is_valid
    assert result.sanitized_value is None


# ---------------------------------------------------------------------------
# Empty / None inputs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [None, "", "   "])
def test_empty_inputs_valid(text):
    result = Validator().validate(text)
    assert result.is_valid
    assert result.sanitized_value == text


# ---------------------------------------------------------------------------
# Custom patterns / add_pattern
# ---------------------------------------------------------------------------


def test_custom_pattern_single():
    v = Validator(blocklist_patterns=[r"foo"])
    assert not v.validate("foo bar").is_valid
    assert v.validate("bar").is_valid


def test_custom_pattern_case_insensitive():
    v = Validator(blocklist_patterns=[r"secret-word"])
    assert not v.validate("contains SECRET-WORD here").is_valid


def test_add_pattern_extends_blocklist():
    v = Validator(blocklist_patterns=[r"foo"])
    assert v.validate("bar baz").is_valid
    v.add_pattern(r"baz")
    assert not v.validate("bar baz").is_valid


def test_multiple_patterns_first_match_reported():
    v = Validator(blocklist_patterns=[r"alpha", r"beta"])
    result = v.validate("beta")
    assert not result.is_valid
    assert "beta" in result.error_message


# ---------------------------------------------------------------------------
# Module-level API
# ---------------------------------------------------------------------------


def test_module_validate_defaults():
    result = validate("plain text")
    assert result.is_valid


def test_module_add_pattern_affects_module_validator():
    from utils.validator import _get_validator

    v = _get_validator()
    before = v.validate("zzz-unique-token-zzz").is_valid
    add_pattern(r"zzz-unique-token-zzz")
    after = v.validate("zzz-unique-token-zzz").is_valid
    assert before is True
    assert after is False


def test_validation_result_attributes():
    r = ValidationResult(True, "x", None)
    assert r.is_valid is True
    assert r.sanitized_value == "x"
    assert r.error_message is None
