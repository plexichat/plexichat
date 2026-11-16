"""Denylist of active-content sinks for short, machine-ish embed fields.

Text body fields are scanned by the broad ``DANGEROUS_PATTERNS`` blocklist in
``constants``. Short fields such as ``color`` and ``provider.name`` also reach
client attribute/CSS sinks and need a compact, explicit list of the constructs
that can turn a benign-looking value into script execution. This module owns
that list so the rule is shared and cannot drift between call sites.
"""

from __future__ import annotations

import re
from typing import Type

from ..exceptions import EmbedSanitizationError

ACTIVE_CONTENT_SINK_PATTERNS = [
    # Elements that create a script/HTML/embedding context.
    re.compile(
        r"<\s*/?\s*(?:script|iframe|object|embed|svg|math|form|link|meta|"
        r"base|template|frame|frameset|applet)\b",
        re.IGNORECASE,
    ),
    # Attribute sinks that carry a second document or a navigation target.
    re.compile(r"\b(?:srcdoc|formaction|xlink:href)\s*=", re.IGNORECASE),
    # CSS script/URL sinks, including obfuscated backslash escapes.
    re.compile(r"\b(?:url|expression)\s*\(", re.IGNORECASE),
    re.compile(r"@import\b", re.IGNORECASE),
    # Any inline event handler attribute.
    re.compile(r"\bon[a-z]{3,}\s*=", re.IGNORECASE),
    # Data/vbscript/javascript pseudo-schemes.
    re.compile(r"\b(?:data|vbscript|javascript)\s*:", re.IGNORECASE),
    # Raw C0 controls and DEL can split tokens or forge markup.
    re.compile(r"[\x00-\x1f\x7f]"),
]


def deny_active_content(
    value: object,
    field: str,
    error_cls: Type[Exception] = EmbedSanitizationError,
) -> str:
    """Return ``value`` as text, refusing any denylisted active-content sink."""
    text = str(value or "")
    for pattern in ACTIVE_CONTENT_SINK_PATTERNS:
        if pattern.search(text):
            raise error_cls(f"Active content not allowed in {field}", field)
    return text
