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

    # Control characters split tokens (e.g. "jav\tascript:"); the stripped
    # form is used for matching only, the returned content is untouched so
    # legitimate multilingual text (Arabic tatweel, emoji ZWJ, ...) is not
    # mutilated.
    normalized_no_ctrl = "".join(
        ch for ch in normalized_full if not (ord(ch) < 0x20 or ord(ch) == 0x7F)
    )
    normalized_no_ws = re.sub(r"\s+", "", normalized_full).lower()

    for target in (
        content,
        decoded,
        normalized_no_ws,
        normalized_no_ctrl.lower(),
        re.sub(r"\s+", "", normalized_no_ctrl).lower(),
    ):
        for pattern in DANGEROUS_PATTERNS:
            if pattern.search(target):
                raise EmbedSanitizationError(
                    f"Potentially unsafe content detected in {field_name}",
                    field_name,
                )

    return content
