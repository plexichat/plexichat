"""
URL validation.

SSRF protection, attribute-breakout defence and URL sanitization for embed
content. Untrusted URLs fail closed: anything that is not an explicitly
allowed absolute http(s) URL or a strictly-shaped server-relative media path
is rejected.
"""

import ipaddress
import unicodedata
from typing import Optional
from urllib.parse import urlparse, urlunparse

try:
    import utils.config as config
except ImportError:
    config = None

from .constants import (
    MAX_URL_LENGTH,
    URL_ATTRIBUTE_BREAKOUT_PATTERN,
    URL_PATTERN,
    _RELATIVE_MEDIA_PATTERN,
    _URL_CONTROL_CHAR_PATTERN,
)
from ..exceptions import InvalidUrlError


def _strip_url_dangerous_unicode(value: str) -> str:
    """Strip invisible / bidi-override characters from URL-shaped strings.

    Scoped to URL contexts where those characters are an attack primitive
    (zero-width inside a hostname, bidi override in a URL displayed in a
    browser status bar). Multilingual text body content is unaffected.
    """
    return "".join(
        ch for ch in value if unicodedata.category(ch) not in ("Cf", "Cc", "Cs")
    )


def _public_media_origin() -> Optional[str]:
    """Return the public absolute origin media URLs should be lifted to.

    Uses ``static_client.config_injection.public_server_url`` when it is
    configured with a public (non-local) host; returns ``None`` when no
    public origin is available so callers can keep the relative form.
    """
    origin = ""
    try:
        static_cfg = (config.get("static_client", {}) if config else {}) or {}
        injection = static_cfg.get("config_injection", {}) or {}
        origin = str(injection.get("public_server_url") or "").strip()
    except Exception:
        origin = ""
    if origin:
        parsed = urlparse(origin)
        host = (parsed.hostname or "").lower()
        if (
            not host
            or host in {"localhost", "127.0.0.1", "::1"}
            or host.endswith((".local", ".internal"))
        ):
            return None
        return f"{parsed.scheme.lower() or 'https'}://{parsed.netloc}"
    return None


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

    # Hardening: bound input length before any regex work (regex-DoS
    # prevention) and reject oversized payloads fail-closed.
    if len(url) > MAX_URL_LENGTH:
        raise InvalidUrlError(
            f"URL exceeds maximum length of {MAX_URL_LENGTH} characters "
            f"in {field_name}",
            url,
        )

    # Check for dangerous protocols
    lower_url = url.lower()
    if lower_url.startswith("javascript:"):
        raise InvalidUrlError(f"JavaScript URLs not allowed in {field_name}", url)
    if lower_url.startswith("data:"):
        raise InvalidUrlError(f"Data URLs not allowed in {field_name}", url)
    if lower_url.startswith("vbscript:"):
        raise InvalidUrlError(f"VBScript URLs not allowed in {field_name}", url)

    # Attribute-breakout defence: reject quote, angle-bracket and backtick
    # characters plus raw control characters BEFORE any normalization,
    # against both the raw input and the unicode-stripped form.
    for candidate in (url, _strip_url_dangerous_unicode(url)):
        if URL_ATTRIBUTE_BREAKOUT_PATTERN.search(candidate):
            raise InvalidUrlError(
                f"URL contains characters not allowed in {field_name}", url
            )
        if _URL_CONTROL_CHAR_PATTERN.search(candidate):
            raise InvalidUrlError(
                f"URL contains control characters in {field_name}", url
            )

    url = _strip_url_dangerous_unicode(url)

    # Server-relative media-pipeline URLs are legitimate embed targets:
    # uploads come back as ``/api/v1/media/...`` paths. Accept ONLY the
    # strictly-shaped form and lift it against the configured public origin.
    if url.startswith("/") and _RELATIVE_MEDIA_PATTERN.match(url):
        if ".." in url.split("/"):
            raise InvalidUrlError(f"Path traversal not allowed in {field_name}", url)
        base = _public_media_origin()
        if not base:
            return url
        url = base.rstrip("/") + url

    # Must be http or https (absolute URLs only from here on)
    if not (url.lower().startswith("http://") or url.lower().startswith("https://")):
        raise InvalidUrlError(
            f"URL must use http or https protocol in {field_name}", url
        )

    # Normalise internationalised (non-ASCII) hostnames to punycode so IDN
    # sites pass the ASCII shape check and resolve correctly downstream.
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

    # SSRF check: delegate to the centralised URLValidator which resolves
    # DNS and blocks private/loopback/link-local/multicast/unspecified
    # ranges. Strict fallback if the security module is unavailable.
    try:
        from src.utils.security import URLValidator

        URLValidator().validate_url_for_request(url)
    except (ImportError, ModuleNotFoundError):
        import utils.logger as _url_logger

        try:
            _url_logger.critical(
                "src.utils.security.URLValidator unavailable; "
                "falling back to raw IP literal block in embed "
                "validation. Install src.utils.security to enable "
                "DNS-based SSRF protection."
            )
        except Exception as config_error:
            # Fail closed on the DNS policy above; record why the config could
            # not be read so the operator can fix it.
            import utils.logger as logger

            logger.debug(f"URL validation config unavailable: {config_error}")

        _urlparse = urlparse
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
                # Not an IP — likely a DNS hostname. Refuse tricky
                # literal-looking names ourselves.
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
