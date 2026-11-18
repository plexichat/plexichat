"""SEC200 invariant: shared hardening primitives give one answer for line breaks.

Control characters that any downstream parser treats as a line terminator must
be rejected at the single choke point in :mod:`src.core.security.hardening`,
not left for each header writer / log shipper / URL client to disagree about.
The forbidden set is deliberately wider than C0 + DEL:

* C1 controls ``\\x80-\\x9f`` (notably U+0085 NEL, "next line");
* U+2028 LINE SEPARATOR and U+2029 PARAGRAPH SEPARATOR, which JavaScript,
  many HTTP stacks and most log aggregators normalise to a newline.

Separately, integer coercion must not inherit ``int()``'s permissive string
grammar: Unicode decimal homoglyphs (``"٤٢"``, ``"４２"``) and PEP 515
underscore separators (``"1_0"``) are rejected by ASCII-only validators at the
next hop, so accepting them here creates a parser differential.

The surface is imported from the package (not a submodule) on purpose: a
helper that stops being re-exported is itself a regression. This suite is pure
logic -- no app import, config or database -- so it is fully deterministic.
"""

import pytest

from src.core.security.hardening import (
    SNOWFLAKE_MAX,
    HardeningViolation,
    bounded_int,
    content_disposition,
    is_identifier,
    parse_id,
    reject_control_characters,
    require_http_url,
    require_https_url,
    require_identifier,
    require_ws_url,
    sanitize_header_filename,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]

# C1 band + the two Unicode separators: characters the historic
# [\x00-\x1f\x7f] pattern missed but that are line breaks/controls for at
# least one downstream parser.
WIDE_CONTROLS = [chr(cp) for cp in range(0x80, 0xA0)] + ["\u2028", "\u2029"]

# Tokens int() happily accepts but that no ASCII-only validator would.
PERMISSIVE_INTEGERS = [
    "٤٢",  # Arabic-Indic digits -> 42
    "４２",  # fullwidth digits -> 42
    "४२",  # Devanagari digits -> 42
    "1_0",  # PEP 515 underscore -> 10
    "1_000",  # PEP 515 grouping -> 1000
    "1٠0",  # mixed ASCII + homoglyph digit
    "1\u20282",  # separator embedded in a numeric token
]


# --- the control-character set itself ---------------------------------------


@pytest.mark.parametrize("control", WIDE_CONTROLS)
def test_reject_control_characters_covers_c1_and_unicode_separators(control):
    with pytest.raises(HardeningViolation):
        reject_control_characters(f"pre{control}post", "field")


@pytest.mark.parametrize("control", ["\x00", "\r", "\n", "\t", "\x1f", "\x7f"])
def test_reject_control_characters_still_covers_c0_and_del(control):
    with pytest.raises(HardeningViolation):
        reject_control_characters(f"pre{control}post", "field")


def test_reject_control_characters_accepts_plain_text():
    assert reject_control_characters("abc123-_./", "field") == "abc123-_./"


# --- every consumer funnels through the same control set --------------------


@pytest.mark.parametrize("control", WIDE_CONTROLS)
@pytest.mark.parametrize(
    "helper", (require_http_url, require_https_url, require_ws_url)
)
def test_url_helpers_reject_wide_control_characters(helper, control):
    # urlparse leaves these characters in the authority/path untouched; the
    # helper must refuse before parsing.
    with pytest.raises(HardeningViolation):
        helper(f"https://example.test/{control}x", "url")


@pytest.mark.parametrize("control", WIDE_CONTROLS)
def test_header_filename_scrubs_wide_control_characters(control):
    cleaned = sanitize_header_filename(f"report{control}2026.txt")
    assert control not in cleaned
    assert "report" in cleaned


@pytest.mark.parametrize("control", WIDE_CONTROLS)
def test_content_disposition_never_emits_wide_control_characters(control):
    value = content_disposition(f"report{control}X-Injected: 1.txt")
    assert control not in value
    assert "\n" not in value and "\r" not in value
    assert value.count('"') == 2  # no bare quote can terminate the header value


# --- integer coercion is ASCII-only -----------------------------------------


@pytest.mark.parametrize("value", PERMISSIVE_INTEGERS)
def test_bounded_int_rejects_permissive_int_string_grammar(value):
    with pytest.raises(HardeningViolation):
        bounded_int(value, "id", minimum=0, maximum=100)


@pytest.mark.parametrize("value", PERMISSIVE_INTEGERS)
def test_parse_id_rejects_permissive_int_string_grammar(value):
    with pytest.raises(HardeningViolation):
        parse_id(value, "id")


@pytest.mark.parametrize("value", ["42", " 42 ", "+7", "-5", "0"])
def test_bounded_int_still_accepts_plain_ascii_integers(value):
    assert bounded_int(value, "value", minimum=-10, maximum=100) == int(value)


def test_parse_id_accepts_the_ascii_upper_bound():
    assert parse_id(str(SNOWFLAKE_MAX)) == SNOWFLAKE_MAX


# --- identifiers stay ASCII even under homoglyph input -----------------------


@pytest.mark.parametrize("value", ["users\u2028", "user٤٢", "us\u0085ers", "users\rx"])
def test_identifiers_reject_controls_and_unicode_digits(value):
    # Underscores are legal *inside* an ASCII identifier; the unicode digits
    # and embedded line separators above are not part of the identifier set.
    assert not is_identifier(value)
    with pytest.raises(HardeningViolation):
        require_identifier(value)
