"""
Content sanitization.

XSS prevention for embed text fields. Content is matched against the
dangerous pattern blocklist in raw, URL-decoded, HTML-unescaped and
NFKC-normalised forms (with and without control characters and
whitespace) so obfuscated payloads cannot slip past.
"""

import html
import re
import unicodedata
import urllib.parse

from .constants import DANGEROUS_PATTERNS
from ..exceptions import EmbedSanitizationError


def _is_invisible(ch: str) -> bool:
    """Whether a character can split a token without being visible to a human.

    NFKC leaves most of these untouched and ``\\s`` does not match them, so an
    explicit pass is required. Covers C0 controls, DEL, C1 controls and every
    Unicode ``Cf`` format character (zero-width space/joiner, bidi controls,
    soft hyphen, word joiner, ...).
    """
    code = ord(ch)
    if code < 0x20 or 0x7F <= code <= 0x9F:
        return True
    return unicodedata.category(ch) == "Cf"


def sanitize_content(content: str, field_name: str = "content") -> str:
    """
    Sanitize text content to prevent XSS.

    Args:
        content: Content to sanitize
        field_name: Name of field for error messages

    Returns:
        Sanitized content

    Raises:
        EmbedSanitizationError: If content contains dangerous patterns
    """
    if not content:
        return content

    # Decode percent-encoding and HTML entities so obfuscated payloads are
    # matched in their decoded form.
    decoded = urllib.parse.unquote(content)
    decoded = html.unescape(decoded)

    # NFKC normalisation collapses lookalike/homoglyph variants.
    normalized_full = unicodedata.normalize("NFKC", decoded)

    # Control and format characters can split a dangerous token without
    # changing how it looks ("java\u200bscript:", "<scr\u200bipt>",
    # "on\u200click="). NFKC leaves most format chars (category Cf: ZWSP,
    # ZWNJ, ZWJ, bidi controls, soft hyphen, ...) intact and ``\s`` does not
    # match them, so strip them explicitly. These forms are used for matching
    # only; the returned content is untouched so legitimate multilingual text
    # (Arabic tatweel, emoji ZWJ, ...) is not mutilated.
    normalized_visible = "".join(ch for ch in normalized_full if not _is_invisible(ch))
    normalized_visible_lower = normalized_visible.lower()
    normalized_squashed = re.sub(r"\s+", "", normalized_visible_lower)

    for target in (
        content,
        decoded,
        normalized_squashed,
        normalized_visible_lower,
    ):
        for pattern in DANGEROUS_PATTERNS:
            if pattern.search(target):
                raise EmbedSanitizationError(
                    f"Potentially unsafe content detected in {field_name}",
                    field_name,
                )

    return content
