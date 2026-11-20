"""Fail-closed guard: SVG / active markup must never be stored or served as an avatar.

Agent7 [L1]: the avatar ``allowed_types`` list is operator-configurable. If an
operator adds ``image/svg+xml``, an attacker-uploaded SVG used to be stored
verbatim and later served inline under the application origin -> stored XSS
on every avatar render. The media pipeline's defusedxml sanitizer was never
wired into the avatars module, so the "FIXED (equivalent design)" claim did
not hold end-to-end for avatars.

This module is the single source of truth for the avatar fail-closed policy:

* :data:`NEVER_ALLOWED_AVATAR_CONTENT_TYPES` cannot be opted into via config.
* Uploads claiming a blocked type are rejected, and payloads that *look*
  like XML/HTML markup are rejected even when the claimed type is allowed
  (MIME-spoofed SVG).
* The serving path refuses to return a ``<script>``-carrying (or otherwise
  active-markup) payload raw; callers fall back to the generated default SVG.
"""

from __future__ import annotations

import html
import re

# Content types that must never be accepted for, or served as, an avatar or
# server icon - regardless of operator configuration. SVG is raster-hostile
# (Pillow cannot decode it, so it would be stored verbatim) and
# browser-executable when served inline; HTML/JS types are included so a
# misconfigured allow-list cannot opt into script execution either.
NEVER_ALLOWED_AVATAR_CONTENT_TYPES = frozenset(
    {
        "image/svg+xml",
        "image/svg",
        "application/svg+xml",
        "text/html",
        "application/xhtml+xml",
        "text/javascript",
        "application/javascript",
        "application/x-javascript",
        "application/ecmascript",
        "text/ecmascript",
    }
)

# Leading-byte signatures (lower-cased, after BOM/whitespace stripping) that
# identify an XML/HTML document. Raster formats (PNG/JPEG/GIF/WebP) never
# start with ``<``, so any avatar payload with such a prefix is either SVG,
# HTML, or garbage - never a legitimate avatar.
_MARKUP_PREFIXES = (
    b"<svg",
    b"<?xml",
    b"<html",
    b"<!doctype html",
    b"<script",
    b"<math",
    b"<foreignobject",
    b"<iframe",
)

# Substrings indicating *active* content inside a markup payload. A plain
# ``<svg><rect/></svg>`` shape placeholder is inert, but anything carrying
# script, event handlers, javascript: URIs, or frame/object embeds must never
# be served raw under our origin.
_ACTIVE_CONTENT_MARKERS = (
    b"<script",
    b"javascript:",
    b"vbscript:",
    b"data:text/html",
    b"<foreignobject",
    b"<iframe",
    b"<embed",
    b"<object",
    b"<link",
    b"<meta",
    b"<base",
    b"onload=",
    b"onerror=",
    b"onclick=",
    b"onmouseover=",
    b"onfocus=",
    b"onbegin=",
)

# The literal ``on<handler>=`` markers above are bypassable two ways a browser
# still treats as executable:
#   * HTML/SVG permit arbitrary whitespace around ``=`` (``onload =...``,
#     ``onerror\t=...``), so the literal marker never matches;
#   * a numeric character reference for ``:`` (``javascript&#58;`` / hex)
#     hides a dangerous scheme from the literal ``javascript:`` marker.
# This guard is the fail-closed classifier for stored (including legacy /
# poisoned) avatar rows, so a miss here means raw script execution under our
# origin. Match the handler name followed by optional whitespace and ``=``,
# and normalise entity-encoded colons before scanning. The explicit name list
# avoids flagging arbitrary binary data the way a broad ``on\w+=`` would.
_ACTIVE_EVENT_RE = re.compile(
    rb"\bon(?:load|error|click|mouseover|mouseenter|mouseleave|mousedown|"
    rb"mouseup|dblclick|focus|blur|change|input|submit|keydown|keyup|"
    rb"keypress|contextmenu|drag|drop|wheel|scroll|toggle|play|playing|"
    rb"pause|begin|end|animationstart|animationend|animationiteration|"
    rb"transitionend|message|storage|hashchange|pageshow|pagehide|"
    rb"beforeunload|reset|invalid|select|abort|canplay|loadeddata|"
    rb"loadedmetadata|progress|ratechange|seeked|seeking|stalled|suspend|"
    rb"timeupdate|volumechange|waiting|copy|paste|cut|pointerdown|"
    rb"pointerup|pointerover|pointerenter|pointermove)\s*=",
    re.IGNORECASE,
)
_ENTITY_COLON_RE = re.compile(rb"&#(?:x0*3a|0*58);?", re.IGNORECASE)


def _html_unescape_bytes(data: bytes) -> bytes:
    """Decode HTML character references in a bytes buffer using Latin-1.

    The serve-time classifier must see the same characters a browser does:
    ``javascript&colon;`` and ``onload&Tab;=`` are executable to a browser
    even though the literal ``javascript:`` / ``onload=`` markers are absent.
    Latin-1 preserves every raw byte 1:1, so the change is limited to
    ``&...;`` sequences. Unmappable characters are dropped (they cannot form
    an ASCII security token). Fail closed on any error by returning the
    original buffer -- the literal marker checks still apply to it.
    """
    try:
        return html.unescape(data.decode("latin-1")).encode("latin-1", "ignore")
    except Exception:
        return data


def normalize_content_type(content_type: str | None) -> str:
    """Lower-case the media type, dropping parameters (``; charset=...``)."""
    return (content_type or "").split(";")[0].strip().lower()


def is_blocked_avatar_content_type(content_type: str | None) -> bool:
    """True when ``content_type`` may never be used for an avatar, any config."""
    return normalize_content_type(content_type) in NEVER_ALLOWED_AVATAR_CONTENT_TYPES


def _leading_head(data: bytes, limit: int = 2048) -> bytes:
    """Return the lower-cased leading bytes with BOM/whitespace stripped."""
    head = (
        bytes(data[:limit])
        if not isinstance(data, (bytes, bytearray))
        else bytes(data[:limit])
    )
    # Strip UTF-8/UTF-16 BOMs then ASCII whitespace.
    for bom in (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"):
        if head.startswith(bom):
            head = head[len(bom) :]
            break
    # Decode HTML references before lower-casing and whitespace stripping:
    # entity names are case-sensitive (``&Tab;`` decodes, ``&tab;`` does not)
    # and ``&Tab;`` itself represents leading whitespace a browser honours.
    return _html_unescape_bytes(head).lstrip(b" \t\r\n\x0b\x0c").lower()


def payload_looks_like_markup(data: bytes) -> bool:
    """True when raw ``data`` starts like an XML/HTML document (incl. SVG)."""
    if not data:
        return False
    try:
        head = _leading_head(data)
    except Exception:
        return False
    return head.startswith(_MARKUP_PREFIXES)


def payload_carries_active_content(data: bytes) -> bool:
    """True when a markup payload contains script-capable constructs.

    Only meaningful for payloads where :func:`payload_looks_like_markup` is
    True; binary raster data is never inspected for these substrings so a
    coincidental byte sequence inside a PNG/JPEG cannot cause a false block.
    """
    if not data:
        return False
    if not payload_looks_like_markup(data):
        return False
    try:
        head = _leading_head(data, limit=65536)
        # ``_leading_head`` already decoded HTML character references (named
        # and numeric) before lower-casing, covering both javascript&colon;
        # and the whitespace reference in ``onload&Tab;=``. Normalise numeric
        # colons once more for defence in depth.
        head = _ENTITY_COLON_RE.sub(b":", head)
    except Exception:
        # Fail closed: an inspected payload we cannot normalise must not be
        # classified as safe.
        return True
    if any(marker in head for marker in _ACTIVE_CONTENT_MARKERS):
        return True
    return _ACTIVE_EVENT_RE.search(head) is not None


def is_unsafe_upload_payload(content_type: str | None, data: bytes) -> bool:
    """True when an avatar upload must be rejected.

    Rejects blocked content types outright, and rejects any payload that
    parses as markup even if the claimed type is an allowed raster type
    (MIME-spoofed SVG such as ``image/png`` bytes starting with ``<svg``).
    """
    if is_blocked_avatar_content_type(content_type):
        return True
    try:
        return payload_looks_like_markup(data)
    except Exception:
        # Fail closed: if we cannot even inspect the bytes, refuse them.
        return True


def is_unsafe_serve_payload(content_type: str | None, data: bytes) -> bool:
    """True when a stored avatar payload must NOT be served raw.

    Covers both honestly-labelled SVG (``image/svg+xml`` with ``<script>``)
    and legacy/spoofed rows (``image/png`` label over an SVG+script body).
    Benign raster bytes are never flagged: the active-content check only
    applies to payloads that actually look like markup.
    """
    if is_blocked_avatar_content_type(content_type):
        return True
    try:
        return payload_carries_active_content(data)
    except Exception:
        return True
