"""
Embed validator - Validation and sanitization for embed content.

Handles field limits, character limits, URL validation, and XSS prevention.
"""

import re
import unicodedata
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

from .exceptions import (
    EmbedValidationError,
    InvalidUrlError,
    InvalidColorError,
    EmbedSanitizationError,
)


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

# URL validation pattern (http/https only)
#
# SECURITY: the previous regex accepted both ``localhost`` and
# raw IPv4 literals like ``http://10.0.0.5``. Either of those in an
# embed/avatar URL became a hard SSRF primitive — anywhere a
# user-supplied URL is fetched server-side (link previews, image
# proxying, embed enrichment), the attacker pointed Plexichat at
# an internal endpoint. Regex alone cannot block IPv6 brackets,
# octal IP forms, or DNS-rebinding. We drop the IP/literal
# allowance entirely here — the centralised URLValidator in
# ``src.utils.security`` is the canonical place that resolves DNS
# and rejects private/loopback/link-local/multicast/unspecified
# ranges. URL_PATTERN here only enforces shape.
#
# NOTE: the TLD is any well-formed DNS label (not just ``[A-Z]{2,6}``)
# so punycode / IDN TLDs (e.g. ``xn--p1ai`` for ``.рф``) are accepted.
# At least one dot is still required (single-label hosts are not
# allowed because they commonly resolve to internal networks); the
# SSRF checks in ``src.utils.security.URLValidator`` remain the
# authoritative gate.
URL_PATTERN = re.compile(
    r"^https?://"
    r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)"
    r"(?::\d+)?"
    r"(?:/?|[/?]\S+)$",
    re.IGNORECASE,
)

# Color validation pattern (hex color)
COLOR_PATTERN = re.compile(r"^#?([0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})$")

# ISO8601 timestamp pattern
TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}"
    r"(?:T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)?$"
)

# Dangerous patterns for XSS prevention.
#
# SECURITY NOTE: these are matched against the raw content, the
# percent/entity-decoded content, AND the whitespace-stripped,
# lower-cased normalization. Token-boundary and tag-context anchors
# keep legitimate prose (e.g. "done=", "phone=", "onboarding=",
# "description") from tripping the blocklist while still catching real
# payloads (``<script>``, ``javascript:``, ``onclick=``, ``<iframe>``,
# ...).
DANGEROUS_PATTERNS = [
    re.compile(r"<\s*script\b", re.IGNORECASE),
    re.compile(r"<\s*(?:iframe|object|embed)\b", re.IGNORECASE),
    re.compile(r"\bjavascript\s*:", re.IGNORECASE),
    re.compile(r"\bvbscript\s*:", re.IGNORECASE),
    re.compile(r"\bdata\s*:", re.IGNORECASE),
    # Event handlers inside an HTML-style tag (whitespace-tolerant).
    re.compile(r"<\s*[^>]*\s*on\w+\s*=", re.IGNORECASE),
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


@dataclass
class ValidationResult:
    """Result of embed validation."""

    valid: bool
    issues: List[str]
    total_chars: int
    sanitized_data: Optional[Dict[str, Any]] = None


def validate_url(url: str, field_name: str = "url") -> str:
    """
    Validate and sanitize a URL.

    Args:
        url: URL to validate
        field_name: Name of field for error messages

    Returns:
        Sanitized URL

    Raises:
        InvalidUrlError: If URL is invalid or unsafe
    """
    if not url:
        return url

    url = url.strip()

    # Check for dangerous protocols
    lower_url = url.lower()
    if lower_url.startswith("javascript:"):
        raise InvalidUrlError(f"JavaScript URLs not allowed in {field_name}", url)
    if lower_url.startswith("data:"):
        raise InvalidUrlError(f"Data URLs not allowed in {field_name}", url)
    if lower_url.startswith("vbscript:"):
        raise InvalidUrlError(f"VBScript URLs not allowed in {field_name}", url)

    # Must be http or https
    if not (lower_url.startswith("http://") or lower_url.startswith("https://")):
        raise InvalidUrlError(
            f"URL must use http or https protocol in {field_name}", url
        )

    # SECURITY: a URL-shaped string is the one context where
    # bidi-override / zero-width characters MUST be stripped before
    # matching. Apply BEFORE the URL_PATTERN check so that an
    # invisible character inside the host doesn't let a malicious
    # URL slip past.
    url = _strip_url_dangerous_unicode(url)

    # Normalise internationalised (non-ASCII) hostnames to their
    # punycode form so IDN sites (e.g. ``https://пример.рф``) pass the
    # ASCII shape check and resolve correctly downstream. The rebuilt
    # URL keeps scheme, port, path, query and fragment intact.
    parsed_host = urlparse(url)
    if parsed_host.hostname and any(ord(c) > 127 for c in parsed_host.hostname):
        try:
            ascii_host = parsed_host.hostname.encode("idna").decode("ascii")
            port = ""
            if parsed_host.port:
                port = f":{parsed_host.port}"
        except (UnicodeError, ValueError):
            raise InvalidUrlError(f"Invalid URL format in {field_name}", url)
        netloc = f"{ascii_host}{port}"
        url = urlunparse(
            (
                parsed_host.scheme,
                netloc,
                parsed_host.path,
                parsed_host.params,
                parsed_host.query,
                parsed_host.fragment,
            )
        )

    # Validate URL shape first
    if not URL_PATTERN.match(url):
        raise InvalidUrlError(f"Invalid URL format in {field_name}", url)

    # SECURITY: reject raw IPv4 / IPv6 literals and ``localhost``-
    # style hostnames. Plexichat frequently fetches the validated URL
    # server-side, so a literal ``http://10.0.0.5`` would otherwise
    # become an SSRF primitive. We delegate the heavy lifting to the
    # centralised URLValidator in src.utils.security which resolves
    # DNS and blocks private/loopback/link-local/multicast ranges.
    try:
        from src.utils.security import URLValidator

        URLValidator().validate_url_for_request(url)
    except (ImportError, ModuleNotFoundError):
        # Strict fallback: if the security module is unavailable we
        # still want to refuse obviously-private IPs by parsing them
        # with the stdlib. We log a CRITICAL so operators notice
        # the missing security module.
        import ipaddress
        from urllib.parse import urlparse as _urlparse

        try:
            import utils.logger as _url_logger

            _url_logger.critical(
                "src.utils.security.URLValidator unavailable; "
                "falling back to raw IP literal block in embed "
                "validation. Install src.utils.security to enable "
                "DNS-based SSRF protection."
            )
        except Exception:
            pass

        parsed_host = _urlparse(url).hostname or ""
        if parsed_host:
            # Strip IPv6 brackets if present.
            bare = parsed_host.strip("[]")
            try:
                ip = ipaddress.ip_address(bare)
                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_reserved
                    or ip.is_multicast
                    or ip.is_unspecified
                ):
                    raise InvalidUrlError(
                        f"Private/reserved IP literal not allowed in {field_name}",
                        url,
                    )
            except ValueError:
                # Not an IP — likely a DNS hostname. We cannot block
                # DNS-rebinding without the security helper; refuse
                # tricky literal-looking names ourselves.
                if bare.lower() in {
                    "localhost",
                    "localhost.localdomain",
                    "local",
                    "ip6-localhost",
                    "ip6-loopback",
                } or bare.lower().endswith((".local", ".internal")):
                    raise InvalidUrlError(
                        f"Localhost-like hostname not allowed in {field_name}",
                        url,
                    )
    except ValueError as ssrf_exc:
        raise InvalidUrlError(f"URL failed SSRF check in {field_name}: {ssrf_exc}", url)

    return url


def validate_color(color: str) -> str:
    """
    Validate and normalize a hex color.

    Args:
        color: Color to validate (with or without #)

    Returns:
        Normalized color with # prefix

    Raises:
        InvalidColorError: If color format is invalid
    """
    if not color:
        return color

    color = color.strip()

    if not COLOR_PATTERN.match(color):
        raise InvalidColorError(
            "Color must be a valid hex color (e.g., #FF0000)", color
        )

    # Normalize to include #
    if not color.startswith("#"):
        color = "#" + color

    # Expand 3-char to 6-char
    if len(color) == 4:
        color = "#" + color[1] * 2 + color[2] * 2 + color[3] * 2

    return color.upper()


def validate_timestamp(timestamp: str) -> str:
    """
    Validate an ISO8601 timestamp.

    Args:
        timestamp: Timestamp to validate

    Returns:
        Validated timestamp

    Raises:
        EmbedValidationError: If timestamp format is invalid
    """
    if not timestamp:
        return timestamp

    timestamp = timestamp.strip()

    if not TIMESTAMP_PATTERN.match(timestamp):
        raise EmbedValidationError("Timestamp must be in ISO8601 format")

    return timestamp


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

    # SECURITY: decode-then-match so obvious bypasses
    # (HTML entities, percent encoding, case toggling, mixed
    # upper/lower) become visible. We do NOT strip zero-width /
    # format-category characters from content text here because
    # those characters are part of legitimate multilingual content
    # (Arabic tatweel, Hebrew niqqud with ZWJ, emoji ZWJ
    # sequences, RTL marks). Lifting the categories globally
    # mutilates user data.
    # The category-strip is instead implemented in
    # ``_strip_url_dangerous_unicode`` and applied ONLY where the
    # content is treated as a URL-shape input.
    import html as _html
    import re as _re
    import urllib.parse as _ulr

    decoded = _ulr.unquote(content)
    decoded = _html.unescape(decoded)
    normalized_full = unicodedata.normalize("NFKC", decoded)
    normalized_no_ws = _re.sub(r"\s+", "", normalized_full).lower()

    # The raw content and each progressively-decoded form are all
    # matched so a payload hidden behind whitespace, entities, or
    # percent-encoding cannot slip past the blocklist.
    for target in (content, decoded, normalized_no_ws):
        for pattern in DANGEROUS_PATTERNS:
            if pattern.search(target):
                raise EmbedSanitizationError(
                    f"Potentially unsafe content detected in {field_name}",
                    field_name,
                )

    return content


def _strip_url_dangerous_unicode(value: str) -> str:
    """Strip invisible / bidi-override characters from URL-shaped strings.

    Mirrors the previous sanitize-time stripping but scoped to URL
    contexts where those characters are an attack primitive (zero-
    width inside a hostname, bidi override in a URL displayed in a
    browser status bar). Multilingual TEXT body content is unaffected.
    """
    import unicodedata as _ud

    return "".join(ch for ch in value if _ud.category(ch) not in ("Cf", "Cc", "Cs"))


def validate_string_length(value: str, max_length: int, field_name: str) -> str:
    """
    Validate string length.

    Args:
        value: String to validate
        max_length: Maximum allowed length
        field_name: Name of field for error messages

    Returns:
        Validated string

    Raises:
        EmbedValidationError: If string exceeds max length
    """
    if not value:
        return value

    if len(value) > max_length:
        raise EmbedValidationError(
            f"{field_name} exceeds maximum length of {max_length} characters",
            [f"{field_name}: {len(value)}/{max_length}"],
        )

    return value


def _coerce_dimension(value: Any, field_name: str, issues: List[str]) -> Optional[int]:
    """
    Coerce a dimension (width/height) to a non-negative integer.

    Malformed values are reported as validation issues rather than
    raising an uncaught ``ValueError``/``TypeError``.

    Args:
        value: Raw dimension value
        field_name: Name of field for error messages
        issues: Issue accumulator

    Returns:
        Coerced non-negative int, or None if missing/invalid
    """
    if value is None or value == "":
        return None

    if isinstance(value, bool):
        issues.append(f"{field_name} must be an integer")
        return None

    try:
        if isinstance(value, int):
            number = value
        elif isinstance(value, float):
            if not value.is_integer():
                issues.append(f"{field_name} must be an integer")
                return None
            number = int(value)
        else:
            number = int(str(value).strip())
    except (TypeError, ValueError):
        issues.append(f"{field_name} must be an integer")
        return None

    if number < 0:
        issues.append(f"{field_name} must be a non-negative integer")
        return None

    return number


def calculate_embed_chars(embed_data: Dict[str, Any]) -> int:
    """
    Calculate total character count for an embed.

    Args:
        embed_data: Embed data dictionary

    Returns:
        Total character count
    """
    total = 0

    if embed_data.get("title"):
        total += len(str(embed_data["title"]))

    if embed_data.get("description"):
        total += len(str(embed_data["description"]))

    if embed_data.get("footer") and embed_data["footer"].get("text"):
        total += len(str(embed_data["footer"]["text"]))

    if embed_data.get("author") and embed_data["author"].get("name"):
        total += len(str(embed_data["author"]["name"]))

    fields = embed_data.get("fields", [])
    if isinstance(fields, list):
        for field in fields:
            if isinstance(field, dict):
                if field.get("name"):
                    total += len(str(field["name"]))
                if field.get("value"):
                    total += len(str(field["value"]))

    return total


def validate_embed_data(embed_data: Dict[str, Any]) -> ValidationResult:
    """
    Validate complete embed data.

    Args:
        embed_data: Embed data dictionary

    Returns:
        ValidationResult with validation status and any issues
    """
    issues = []
    sanitized = {}

    # Validate title
    if embed_data.get("title"):
        try:
            title = sanitize_content(str(embed_data["title"]), "title")
            title = validate_string_length(title, TITLE_MAX_LENGTH, "title")
            sanitized["title"] = title
        except (EmbedValidationError, EmbedSanitizationError) as e:
            issues.append(str(e))

    # Validate description
    if embed_data.get("description"):
        try:
            desc = sanitize_content(str(embed_data["description"]), "description")
            desc = validate_string_length(desc, DESCRIPTION_MAX_LENGTH, "description")
            sanitized["description"] = desc
        except (EmbedValidationError, EmbedSanitizationError) as e:
            issues.append(str(e))

    # Validate URL
    if embed_data.get("url"):
        try:
            sanitized["url"] = validate_url(str(embed_data["url"]), "url")
        except InvalidUrlError as e:
            issues.append(str(e))

    # Validate timestamp
    if embed_data.get("timestamp"):
        try:
            sanitized["timestamp"] = validate_timestamp(str(embed_data["timestamp"]))
        except EmbedValidationError as e:
            issues.append(str(e))

    # Validate color
    if embed_data.get("color"):
        try:
            sanitized["color"] = validate_color(str(embed_data["color"]))
        except InvalidColorError as e:
            issues.append(str(e))

    # Validate footer
    if embed_data.get("footer"):
        footer = embed_data["footer"]
        sanitized_footer = {}
        if isinstance(footer, dict):
            if footer.get("text"):
                try:
                    text = sanitize_content(str(footer["text"]), "footer.text")
                    text = validate_string_length(
                        text, FOOTER_TEXT_MAX_LENGTH, "footer.text"
                    )
                    sanitized_footer["text"] = text
                except (EmbedValidationError, EmbedSanitizationError) as e:
                    issues.append(str(e))

            if footer.get("icon_url"):
                try:
                    sanitized_footer["icon_url"] = validate_url(
                        str(footer["icon_url"]), "footer.icon_url"
                    )
                except InvalidUrlError as e:
                    issues.append(str(e))
        else:
            issues.append("footer must be an object")

        if sanitized_footer:
            sanitized["footer"] = sanitized_footer

    # Validate image
    if embed_data.get("image"):
        image = embed_data["image"]
        sanitized_image = {}
        if isinstance(image, dict):
            if image.get("url"):
                try:
                    sanitized_image["url"] = validate_url(
                        str(image["url"]), "image.url"
                    )
                except InvalidUrlError as e:
                    issues.append(str(e))

            width = _coerce_dimension(image.get("width"), "image.width", issues)
            if width is not None:
                sanitized_image["width"] = width
            height = _coerce_dimension(image.get("height"), "image.height", issues)
            if height is not None:
                sanitized_image["height"] = height
        else:
            issues.append("image must be an object")

        if sanitized_image:
            sanitized["image"] = sanitized_image

    # Validate thumbnail
    if embed_data.get("thumbnail"):
        thumbnail = embed_data["thumbnail"]
        sanitized_thumbnail = {}
        if isinstance(thumbnail, dict):
            if thumbnail.get("url"):
                try:
                    sanitized_thumbnail["url"] = validate_url(
                        str(thumbnail["url"]), "thumbnail.url"
                    )
                except InvalidUrlError as e:
                    issues.append(str(e))

            width = _coerce_dimension(thumbnail.get("width"), "thumbnail.width", issues)
            if width is not None:
                sanitized_thumbnail["width"] = width
            height = _coerce_dimension(
                thumbnail.get("height"), "thumbnail.height", issues
            )
            if height is not None:
                sanitized_thumbnail["height"] = height
        else:
            issues.append("thumbnail must be an object")

        if sanitized_thumbnail:
            sanitized["thumbnail"] = sanitized_thumbnail

    # Validate author
    if embed_data.get("author"):
        author = embed_data["author"]
        sanitized_author = {}
        if isinstance(author, dict):
            if author.get("name"):
                try:
                    name = sanitize_content(str(author["name"]), "author.name")
                    name = validate_string_length(
                        name, AUTHOR_NAME_MAX_LENGTH, "author.name"
                    )
                    sanitized_author["name"] = name
                except (EmbedValidationError, EmbedSanitizationError) as e:
                    issues.append(str(e))

            if author.get("url"):
                try:
                    sanitized_author["url"] = validate_url(
                        str(author["url"]), "author.url"
                    )
                except InvalidUrlError as e:
                    issues.append(str(e))

            if author.get("icon_url"):
                try:
                    sanitized_author["icon_url"] = validate_url(
                        str(author["icon_url"]), "author.icon_url"
                    )
                except InvalidUrlError as e:
                    issues.append(str(e))
        else:
            issues.append("author must be an object")

        if sanitized_author:
            sanitized["author"] = sanitized_author

    # Validate provider
    if embed_data.get("provider"):
        provider = embed_data["provider"]
        sanitized_provider = {}
        if isinstance(provider, dict):
            if provider.get("name"):
                try:
                    sanitized_provider["name"] = sanitize_content(
                        str(provider["name"]), "provider.name"
                    )
                except EmbedSanitizationError as e:
                    issues.append(str(e))

            if provider.get("url"):
                try:
                    sanitized_provider["url"] = validate_url(
                        str(provider["url"]), "provider.url"
                    )
                except InvalidUrlError as e:
                    issues.append(str(e))
        else:
            issues.append("provider must be an object")

        if sanitized_provider:
            sanitized["provider"] = sanitized_provider

    # Validate fields
    fields = embed_data.get("fields", [])
    if not isinstance(fields, list):
        issues.append("fields must be a list")
    elif len(fields) > MAX_FIELDS:
        issues.append(f"Embed cannot have more than {MAX_FIELDS} fields")
    else:
        sanitized_fields = []
        for i, field in enumerate(fields):
            sanitized_field = {}

            if not isinstance(field, dict):
                issues.append(f"fields[{i}] must be an object")
                continue

            if field.get("name") is not None:
                try:
                    name = sanitize_content(str(field["name"]), f"fields[{i}].name")
                    name = validate_string_length(
                        name, FIELD_NAME_MAX_LENGTH, f"fields[{i}].name"
                    )
                    sanitized_field["name"] = name
                except (EmbedValidationError, EmbedSanitizationError) as e:
                    issues.append(str(e))
            else:
                issues.append(f"fields[{i}].name is required")

            if field.get("value") is not None:
                try:
                    value = sanitize_content(str(field["value"]), f"fields[{i}].value")
                    value = validate_string_length(
                        value, FIELD_VALUE_MAX_LENGTH, f"fields[{i}].value"
                    )
                    sanitized_field["value"] = value
                except (EmbedValidationError, EmbedSanitizationError) as e:
                    issues.append(str(e))
            else:
                issues.append(f"fields[{i}].value is required")

            sanitized_field["inline"] = bool(field.get("inline", False))
            sanitized_fields.append(sanitized_field)

        if sanitized_fields:
            sanitized["fields"] = sanitized_fields

    # Calculate total characters
    total_chars = calculate_embed_chars(sanitized if sanitized else embed_data)

    if total_chars > TOTAL_CHAR_LIMIT:
        issues.append(
            f"Total embed characters ({total_chars}) exceeds limit of {TOTAL_CHAR_LIMIT}"
        )

    return ValidationResult(
        valid=len(issues) == 0,
        issues=issues,
        total_chars=total_chars,
        sanitized_data=sanitized if len(issues) == 0 else None,
    )
