"""
Embed validation constants.

Limits, prefixes, defaults and compiled validation rules for embed content.
"""

import re
from re import Pattern
from typing import List

# Character limits for embeds
TITLE_MAX_LENGTH = 256
DESCRIPTION_MAX_LENGTH = 4096
FIELD_NAME_MAX_LENGTH = 256
FIELD_VALUE_MAX_LENGTH = 1024
FOOTER_TEXT_MAX_LENGTH = 2048
AUTHOR_NAME_MAX_LENGTH = 256
TOTAL_CHAR_LIMIT = 6000
MAX_FIELDS = 25
MAX_EMBEDS_PER_MESSAGE = 10

# Hardening: hard cap on any URL accepted by validate_url. Bounded input is
# the primary regex denial-of-service prevention for the URL shape rules and
# rejects oversized payloads fail-closed.
MAX_URL_LENGTH = 2048

# URL validation pattern (http/https only). Shape only: DNS/SSRF gating is
# delegated to src.utils.security.URLValidator.
URL_PATTERN: Pattern[str] = re.compile(
    r"^https?://"
    r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)"
    r"(?::\d+)?"
    r"(?:/?|[/?]\S+)$",
    re.IGNORECASE,
)

# Characters that enable HTML attribute breakout when a URL is interpolated
# into a quoted src/href attribute on the client.
URL_ATTRIBUTE_BREAKOUT_PATTERN: Pattern[str] = re.compile(r"""["'<>`]""")

# Raw C0 control characters and DEL: never legitimate in a URL here.
_URL_CONTROL_CHAR_PATTERN: Pattern[str] = re.compile(r"[\x00-\x1f\x7f]")

# Server-relative media-pipeline paths accepted by validate_url.
_RELATIVE_MEDIA_PATTERN: Pattern[str] = re.compile(r"^/api/v1/media/[A-Za-z0-9._\-/]*$")

# Color validation pattern (hex color)
COLOR_PATTERN: Pattern[str] = re.compile(r"^#?([0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})$")

# ISO8601 timestamp pattern
TIMESTAMP_PATTERN: Pattern[str] = re.compile(
    r"^\d{4}-\d{2}-\d{2}"
    r"(?:T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)?$"
)

# Dangerous patterns for XSS prevention. Matched against raw, decoded and
# normalised forms of content so whitespace/entity/percent-encoded bypasses
# are caught. Token-boundary anchors keep benign prose (e.g. "done=",
# "phone=", "onboarding=") from tripping the blocklist.
DANGEROUS_PATTERNS: List[Pattern[str]] = [
    re.compile(r"<\s*script\b", re.IGNORECASE),
    re.compile(r"<\s*(?:iframe|object|embed)\b", re.IGNORECASE),
    re.compile(r"\bjavascript\s*:", re.IGNORECASE),
    re.compile(r"\bvbscript\s*:", re.IGNORECASE),
    re.compile(r"\bdata\s*:", re.IGNORECASE),
    # Event handlers inside an HTML-style tag (whitespace-tolerant).
    re.compile(r"<\s*[^>]*\s*on\w+\s*=", re.IGNORECASE),
    # Active-content / unknown-element tags: mXSS/DOM-clobbering and
    # framing primitives that legitimate embed text never needs as markup.
    re.compile(
        r"<\s*/?\s*(?:form|math|base|template|noscript|frame|frameset|"
        r"applet|meta|link|object|embed)\b",
        re.IGNORECASE,
    ),
    # Known standalone event-handler attributes without surrounding tags.
    re.compile(
        r"\bon(?:click|auxclick|dblclick|load|error|abort|focus|blur|"
        r"focusin|focusout|change|input|submit|reset|select|selectstart|"
        r"keydown|keypress|keyup|mousedown|mouseup|mouseover|mouseout|"
        r"mousemove|mouseenter|mouseleave|contextmenu|pointerdown|"
        r"pointerup|pointermove|pointerover|pointerout|pointerenter|"
        r"pointerleave|pointercancel|gotpointercapture|lostpointercapture|"
        r"touchstart|touchend|touchmove|touchcancel|scroll|wheel|drag|"
        r"dragstart|dragend|dragenter|dragover|dragleave|drop|copy|cut|"
        r"paste|animationstart|animationend|animationiteration|"
        r"transitionend|toggle|open|close|invalid|message|online|offline|"
        r"storage|popstate|hashchange|resize|timeupdate|volumechange|"
        r"canplay|canplaythrough|durationchange|emptied|ended|loadeddata|"
        r"loadedmetadata|loadstart|pause|play|playing|progress|ratechange|"
        r"seeked|seeking|stalled|suspend|waiting|cuechange|"
        r"fullscreenchange"
        r")\b\s*=",
        re.IGNORECASE,
    ),
]
