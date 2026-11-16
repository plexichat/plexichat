"""Shared, fail-closed guards for every media upload surface.

Avatars, icons, custom emojis, soundboard sounds and stickers all accept
client-supplied bytes with a client-supplied ``Content-Type``. A caller may
lie about either half of that pair, so this module is the single source of
truth for the defensive checks that must hold on *all* of them:

* the declared media type must agree with the bytes' magic signature;
* a payload may not be a *polyglot* (two container signatures, or a valid
  container with markup smuggled inside it);
* a payload may not carry active content (``<script>``, event handlers,
  ``javascript:`` URIs, ...) in any position, not just at the front.

Every check fails closed: anything that cannot be inspected is rejected.
The module deliberately has no heavyweight imports (Pillow, the database,
config) so it can be imported from route and manager layers alike without
side effects.
"""

from __future__ import annotations

import json
import re

__all__ = [
    "UploadContentError",
    "normalize_mime",
    "detect_containers",
    "contains_active_content",
    "is_markup_payload",
    "is_polyglot",
    "assert_safe_media_payload",
    "declared_type_matches_bytes",
    "verify_upload_content",
    "serve_payload_is_safe",
    "BLOCKED_MIMES",
]


class UploadContentError(ValueError):
    """Raised when uploaded bytes do not honestly match their declaration."""


# Content types that must never be accepted for, or served as, raster media.
# Kept in sync with ``svg_guard`` but local so this module stays independent.
BLOCKED_MIMES = frozenset(
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

_UNSPECIFIED_MIMES = frozenset({"", "application/octet-stream", "binary/octet-stream"})

# How much of the payload to inspect. The upload ceilings everywhere in the
# app are far below this (512 KiB - 5 MiB), so a 4 MiB scan covers the whole
# body while still bounding work if a caller ever hands us a larger buffer.
_SCAN_LIMIT = 4 * 1024 * 1024

# A leading markup prefix anywhere in the scanned head means the payload is
# a document, never an opaque image/audio container.
_MARKUP_PREFIXES = (
    b"<svg",
    b"<?xml",
    b"<html",
    b"<!doctype",
    b"<!entity",
    b"<foreignobject",
)

# Active-content tokens, checked case-insensitively across the whole scanned
# head (not only the beginning). This is what catches a polyglot such as a
# valid GIF followed by ``<script>`` payload data.
_ACTIVE_TOKENS = (
    b"<script",
    b"</script",
    b"javascript:",
    b"vbscript:",
    b"data:text/html",
    b"onload=",
    b"onerror=",
    b"onclick=",
    b"onmouseover=",
    b"onmouseenter=",
    b"onfocus=",
    b"onblur=",
    b"onbegin=",
    b"onend=",
    b"onanimationstart=",
    b"<foreignobject",
    b"<iframe",
    b"<embed",
    b"<object",
    b"<link",
    b"<meta",
    b"<base",
)

# HTML numeric-entity obfuscation of the colon in "javascript:" etc.
_ENTITY_COLON = re.compile(rb"&#(?:x0*3a|0*58);?", re.IGNORECASE)

# Declared media type -> set of container markers that satisfy it. ``riff``
# is accepted for webp/wav because a truncated RIFF header is ambiguous and
# existing valid-upload fixtures rely on that tolerance.
_MIME_TO_CONTAINERS: dict[str, frozenset[str]] = {
    "image/png": frozenset({"png"}),
    "image/apng": frozenset({"png"}),
    "image/gif": frozenset({"gif"}),
    "image/jpeg": frozenset({"jpeg"}),
    "image/jpg": frozenset({"jpeg"}),
    "image/webp": frozenset({"webp", "riff"}),
    "audio/mpeg": frozenset({"mp3"}),
    "audio/mp3": frozenset({"mp3"}),
    "audio/ogg": frozenset({"ogg"}),
    "audio/wav": frozenset({"wav", "riff"}),
    "audio/x-wav": frozenset({"wav", "riff"}),
    "audio/wave": frozenset({"wav", "riff"}),
    "audio/webm": frozenset({"webm"}),
    "application/json": frozenset({"json"}),
}

# Long, low-collision signatures that can be searched for anywhere in the
# scanned head. Short patterns (e.g. JPEG's 3-byte marker or the loose
# ``0xFF 0xEx`` MP3 sync) are intentionally excluded to avoid false positives
# inside otherwise random compressed data.
_EMBEDDED_SIGNATURES: dict[bytes, str] = {
    b"\x89PNG\r\n\x1a\n": "png",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
    b"OggS": "ogg",
    b"WAVE": "wav",
    b"WEBP": "webp",
    b"ID3": "mp3",
    b"\x1aE\xdf\xa3": "webm",
    b"<!doctype": "markup",
    b"<svg": "markup",
    b"<html": "markup",
    b"<?xml": "markup",
}

_BINARY_CONTAINERS = frozenset(
    {"png", "gif", "jpeg", "webp", "wav", "mp3", "ogg", "webm", "riff"}
)


def normalize_mime(content_type: str | None) -> str:
    """Lower-case a media type, dropping any ``; charset=...`` parameters."""
    return (content_type or "").split(";", 1)[0].strip().lower()


def _head(data: bytes, limit: int = _SCAN_LIMIT) -> bytes:
    return bytes(data[:limit]).lower()


def _markers_from_head(data: bytes, head: bytes) -> frozenset[str]:
    """Derive container markers from an already-normalised scan head.

    Split out so every guard can share a single lower-cased copy of a payload
    (up to 4 MiB) instead of rebuilding it for each individual check.
    """
    found: set[str] = set()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        found.add("png")
    elif data.startswith((b"GIF87a", b"GIF89a")):
        found.add("gif")
    elif data.startswith(b"\xff\xd8\xff"):
        found.add("jpeg")
    elif data.startswith(b"RIFF"):
        if data[8:12] == b"WEBP":
            found.add("webp")
        elif data[8:12] == b"WAVE":
            found.add("wav")
        else:
            found.add("riff")
    elif data.startswith(b"ID3") or (
        len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0
    ):
        found.add("mp3")
    elif data.startswith(b"OggS"):
        found.add("ogg")
    elif data.startswith(b"\x1aE\xdf\xa3"):
        found.add("webm")

    if any(prefix in head for prefix in _MARKUP_PREFIXES):
        found.add("markup")
    # Find a second container smuggled after the leading one (the classic
    # polyglot trick). Only high-entropy signatures are searched to keep
    # false positives inside compressed image/audio data negligible.
    for signature, marker in _EMBEDDED_SIGNATURES.items():
        if signature.lower() in head:
            found.add(marker)
    if head.lstrip().startswith((b"{", b"[")):
        try:
            json.loads(bytes(data).decode("utf-8"))
            found.add("json")
        except Exception:
            pass
    return frozenset(found)


def _scan_payload(data: bytes) -> tuple[frozenset[str], bytes]:
    """Inspect a payload once: return (container markers, active-check head).

    Building the lower-cased scan head is the costly part of every guard (up
    to 4 MiB is copied and normalised). Sharing it lets the serve path do the
    whole defense-in-depth check with a single allocation instead of four
    separate whole-buffer passes.
    """
    if not data:
        return frozenset(), b""
    head = _head(data)
    return _markers_from_head(data, head), _ENTITY_COLON.sub(b":", head)


def _has_active_content(active_head: bytes) -> bool:
    return any(token in active_head for token in _ACTIVE_TOKENS)


def _is_polyglot_markers(markers: frozenset[str]) -> bool:
    binary = markers & _BINARY_CONTAINERS
    if len(binary) > 1:
        return True
    return bool(binary and "markup" in markers)


def detect_containers(data: bytes) -> frozenset[str]:
    """Identify every container signature present in ``data``.

    Both the leading magic bytes and markup/JSON markers anywhere in the
    scanned head are considered, so a polyglot reports more than one marker.
    """
    if not data:
        return frozenset()
    return _markers_from_head(data, _head(data))


def contains_active_content(data: bytes) -> bool:
    """True when the payload carries script-capable constructs anywhere."""
    if not data:
        return False
    return _has_active_content(_scan_payload(data)[1])


def is_markup_payload(data: bytes) -> bool:
    """True when the payload looks like an XML/HTML document (incl. SVG)."""
    return "markup" in detect_containers(data)


def is_polyglot(data: bytes) -> bool:
    """True when ``data`` tries to be two formats at once.

    Rejects both a payload with two distinct binary container signatures and
    a valid binary container that also carries markup (e.g. a GIF whose tail
    is an SVG/HTML document).
    """
    return _is_polyglot_markers(detect_containers(data))


def serve_payload_is_safe(content_type: str | None, data: bytes) -> bool:
    """Single-pass equivalent of the route's defense-in-depth serve check.

    Mirrors ``is_serve_safe`` (blocked type -> markup -> polyglot -> active
    content) but scans the payload once. Fail closed on inspection errors.
    """
    if normalize_mime(content_type) in BLOCKED_MIMES:
        return False
    try:
        markers, active_head = _scan_payload(data)
    except Exception:
        return False
    if "markup" in markers:
        return False
    if _is_polyglot_markers(markers):
        return False
    return not _has_active_content(active_head)


def declared_type_matches_bytes(content_type: str | None, data: bytes) -> bool:
    """True when the declared type agrees with the payload's magic bytes.

    Unknown types (e.g. an operator-allowed TIFF) carry no signature in this
    guard, so they are left to the image decoder rather than rejected here.
    """
    required = _MIME_TO_CONTAINERS.get(normalize_mime(content_type))
    if required is None:
        return True
    return bool(detect_containers(data) & required)


def assert_safe_media_payload(data: bytes) -> None:
    """Reject markup, polyglots and active content without checking MIME.

    Used at the HTTP boundary, where a merely *malformed* image should keep
    flowing to the manager's existing Pillow validation (which returns an
    honest generic 400), but an actively malicious payload must be stopped.
    """
    markers, active_head = _scan_payload(data)
    if "markup" in markers:
        raise UploadContentError("Markup documents are not allowed")
    if _has_active_content(active_head) or _is_polyglot_markers(markers):
        raise UploadContentError("Active or polyglot content detected")


def verify_upload_content(
    content_type: str | None,
    data: bytes,
    *,
    expected_mime: str | None = None,
    allow_unspecified: bool = False,
) -> str:
    """Verify declared type, magic bytes and absence of active content.

    Returns the normalised declared media type on success. Raises
    :class:`UploadContentError` (fail closed) on the first failing check.

    ``expected_mime`` overrides the declared type for the magic-byte match;
    managers use it to pin the type to the format they actually detected.
    ``allow_unspecified`` tolerates an absent/octet-stream declaration, which
    some older clients send for otherwise valid bytes.
    """
    normalized = normalize_mime(content_type)
    if normalized in BLOCKED_MIMES:
        raise UploadContentError("Active document content types are not allowed")

    markers, active_head = _scan_payload(data)
    if "markup" in markers:
        raise UploadContentError("Markup documents are not allowed")
    if _has_active_content(active_head) or _is_polyglot_markers(markers):
        raise UploadContentError("Active or polyglot content detected")

    expected = normalize_mime(expected_mime) if expected_mime else normalized
    if expected in _UNSPECIFIED_MIMES:
        if allow_unspecified:
            return normalized or "application/octet-stream"
        raise UploadContentError("Missing content type")
    required = _MIME_TO_CONTAINERS.get(expected)
    if required is None:
        raise UploadContentError(f"Unsupported media type: {expected}")
    if not (markers & required):
        raise UploadContentError("Content does not match declared media type")
    return normalized
