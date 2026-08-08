"""Dense matrix tests for the text ``Validator`` (SQL/XSS blocklist, escape
handling, HTML auto-sanitization).

Each row asserts a concrete, documented behavior of the validator's
``validate()`` contract.
"""

import pytest

from utils.validator import Validator


# ---------------------------------------------------------------------------
# XSS blocklist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "<script>alert(1)</script>",
        "<SCRIPT>alert(1)</SCRIPT>",
        '<script src="https://evil.example/x.js"></script>',
        "javascript:alert(1)",
        "JAVASCRIPT:alert(1)",
        "foo javascript:alert(1) bar",
        "onerror=alert(1)",
        "onClick=alert(1)",
        "onload = steal()",
        '<iframe src="https://evil.example"></iframe>',
        "<IFRAME>",
        '<object data="x"></object>',
        "<OBJECT>",
    ],
)
def test_xss_patterns_rejected(text):
    result = Validator().validate(text)
    assert result.is_valid is False
    assert "blocked pattern" in result.error_message


@pytest.mark.parametrize(
    "text",
    [
        "plain message",
        "hello world",
        "<b>bold text</b>",  # harmless tag: not on the blocklist
        "a < b but not a tag",
        "3 > 2 and 1 < 2",
    ],
)
def test_harmless_text_accepted(text):
    assert Validator().validate(text).is_valid is True


# ---------------------------------------------------------------------------
# SQL injection blocklist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "UNION SELECT password FROM users",
        "union select 1",
        "SELECT * FROM users",
        "select id from users where x",
        "INSERT INTO logs VALUES (1)",
        "insert into t",
        "UPDATE users SET admin=1",
        "update t set x = 1",
        "DELETE FROM users",
        "delete from t",
        "DROP TABLE users",
        "drop table if exists x",
        "name' OR '1'='1",
        "x OR 1=1 --",
        "admin' --",
        "x; DROP TABLE y;",
        "SELECT 1;",
        "value; ",
    ],
)
def test_sql_injection_patterns_rejected(text):
    result = Validator().validate(text)
    assert result.is_valid is False
    assert "blocked pattern" in result.error_message


@pytest.mark.parametrize(
    "text",
    [
        "order",
        "selectable",
        "updateable",
        "insert",
        "dropped table",
        "x = y",  # no OR keyword
    ],
)
def test_sql_adjacent_words_accepted(text):
    # Words that merely CONTAIN sql keywords (or use '=' without OR) are fine.
    assert Validator().validate(text).is_valid is True


# ---------------------------------------------------------------------------
# Escaped-content semantics (allow_escaped / escape_char)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        '"DROP TABLE users"',
        '"SELECT * FROM users"',
        '"UNION SELECT 1"',
        '"DELETE FROM logs"',
    ],
)
def test_escaped_content_allowed_by_default(text):
    result = Validator().validate(text)
    assert result.is_valid is True
    # Escaped content round-trips unchanged (quotes preserved).
    assert result.sanitized_value == text


def test_escaped_disabled_blocks_quoted_content():
    v = Validator(allow_escaped=False)
    assert v.validate('"DROP TABLE users"').is_valid is False


@pytest.mark.parametrize(
    "text",
    [
        '"safe" DROP TABLE "more"',  # pattern sits outside quoted segments
        '"DROP TABLE" users',  # trailing unquoted word -> not fully enclosed
        'x "DROP TABLE users"',  # not enclosed at the start
        '"DROP TABLE users" x',  # not enclosed at the end
    ],
)
def test_partially_quoted_content_still_blocked(text):
    assert Validator().validate(text).is_valid is False


def test_custom_escape_char():
    v = Validator(escape_char="`")
    result = v.validate("`DROP TABLE users`")
    assert result.is_valid is True
    assert result.sanitized_value == "`DROP TABLE users`"


def test_custom_escape_char_still_blocks_partial():
    v = Validator(escape_char="`")
    assert v.validate("`safe` DROP TABLE `more`").is_valid is False


def test_empty_escape_char_disables_escape_path():
    v = Validator(escape_char="")
    assert v.validate('"DROP TABLE users"').is_valid is False


# ---------------------------------------------------------------------------
# HTML auto-sanitization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("hello", "hello"),
        ("a < b", "a &lt; b"),
        ("x & y", "x &amp; y"),
        ("<b>bold</b>", "&lt;b&gt;bold&lt;/b&gt;"),
        ('Say "hello"', "Say &quot;hello&quot;"),
        ("it's", "it&#x27;s"),
    ],
)
def test_html_auto_sanitize(text, expected):
    result = Validator().validate(text)
    assert result.is_valid is True
    assert result.sanitized_value == expected


def test_sanitize_disabled_keeps_text():
    v = Validator(auto_sanitize_html=False)
    result = v.validate("<b>bold</b>")
    assert result.is_valid is True
    assert result.sanitized_value == "<b>bold</b>"


def test_plain_quoted_text_escapes_quotes():
    # Without a blocked pattern there is nothing to "escape" semantically,
    # so quotes are escaped like any other HTML-significant character.
    result = Validator().validate('"hello"')
    assert result.sanitized_value == "&quot;hello&quot;"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, None),
        ("", ""),
    ],
)
def test_empty_input_valid(text, expected):
    result = Validator().validate(text)
    assert result.is_valid is True
    assert result.sanitized_value == expected


# ---------------------------------------------------------------------------
# add_pattern
# ---------------------------------------------------------------------------


def test_add_pattern_blocks_new_terms():
    v = Validator()
    v.add_pattern(r"\bCUSTOM_TOKEN\b")
    assert v.validate("CUSTOM_TOKEN").is_valid is False
    assert v.validate("custom_token_elsewhere").is_valid is True


def test_add_pattern_case_insensitive():
    v = Validator()
    v.add_pattern(r"\bsecret\b")
    assert v.validate("SECRET").is_valid is False


def test_custom_blocklist_replaces_defaults():
    v = Validator(blocklist_patterns=[r"\bforbidden\b"])
    assert v.validate("forbidden").is_valid is False
    # Default patterns are gone.
    assert v.validate("<script>alert(1)</script>").is_valid is True
