"""
Outbound webhook endpoint URL safety (SSRF protection) and log redaction.

The hardened ``URLValidator`` from :mod:`src.utils.security` (scheme
allowlist http/https, private/loopback/CGNAT/NAT64 IP blocking,
resolved-IP checking) guards every outbound webhook endpoint URL:

* at enqueue time (:meth:`WebhookRetryQueue.enqueue`) so an unsafe
  endpoint never even lands in the retry table;
* again at delivery time (:meth:`WebhookDeliverer.deliver`) immediately
  before the HTTP call, so a record written by an older version or a
  tampered row can never be posted to an internal target.

``redact_url`` strips credentials/query strings before any endpoint URL
reaches the logs.
"""

from typing import Tuple
from urllib.parse import urljoin, urlparse

import utils.logger as logger

_ALLOWED_SCHEMES = {"http", "https"}
MAX_ENDPOINT_URL_LENGTH = 2048
MAX_HOSTNAME_LENGTH = 253
MAX_REDIRECT_LOCATION_LENGTH = 2048

# The private-network seam exists solely so loopback end-to-end tests can bind
# a local server. It is deliberately narrowed to loopback literals: an
# accidentally-enabled flag must never become "allow any RFC1918 / link-local
# / cloud-metadata target".
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def redact_url(url: str | None) -> str:
    """Return a log-safe version of ``url``.

    Strips userinfo (credentials) and the query/fragment (which often
    carry tokens), keeping only scheme, host and path.
    """
    if not url:
        return "<missing-url>"
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc
        if "@" in netloc:
            netloc = netloc.rsplit("@", 1)[1]
        return f"{parsed.scheme or 'scheme'}://{netloc}{parsed.path or ''}"
    except Exception:
        return "<unparseable-url>"


def validate_delivery_endpoint(
    url: str, *, allow_private_network: bool = False
) -> Tuple[str, str]:
    """Validate an outbound webhook endpoint URL for SSRF safety.

    Args:
        url: The stored endpoint URL about to be POSTed to.
        allow_private_network: Testing seam ONLY (used by the local
            loopback end-to-end delivery tests). Production callers must
            leave this at its strict default.

    Returns:
        Tuple of ``(hostname, resolved_ip)`` on success.

    Raises:
        ValueError: When the URL fails validation (wrong scheme, blocked
            hostname, resolves to a private/reserved address, or DNS
            failure).
    """
    if not isinstance(url, str) or not url or len(url) > MAX_ENDPOINT_URL_LENGTH:
        raise ValueError("Invalid endpoint URL: empty, non-string or too long")

    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"Endpoint scheme {scheme!r} not allowed; only http/https supported"
        )
    hostname = parsed.hostname
    if not hostname or len(hostname) > MAX_HOSTNAME_LENGTH:
        raise ValueError("Invalid endpoint URL: missing or overlong hostname")

    # Reject malformed / out-of-range ports before the validator or the HTTP
    # client ever sees them. ``parsed.port`` raises ValueError on junk ports.
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid endpoint URL port: {exc}") from exc
    if port is not None and not (1 <= port <= 65535):
        raise ValueError("Invalid endpoint URL port")

    try:
        from src.utils.security import URLValidator
    except ImportError:
        # SECURITY: fail CLOSED. Without the validator we cannot prove
        # the endpoint is not an internal target, so refuse the delivery.
        logger.error(
            "URLValidator unavailable; refusing outbound webhook delivery "
            "(SSRF validation could not run)."
        )
        raise ValueError("Endpoint URL rejected: security validation unavailable")

    if allow_private_network:
        # Test seam: keep the scheme/host/port checks above but permit a
        # loopback target so end-to-end transport tests can bind a local
        # server. Non-loopback hosts stay blocked even with the flag on.
        if hostname.lower() not in _LOOPBACK_HOSTS:
            raise ValueError(
                "Private-network testing seam only permits loopback hosts"
            )
        return hostname, ""

    validator = URLValidator()
    hostname, resolved_ip = validator.validate_url_for_request(url)
    # Validate every resolved address, not just the first one returned: the
    # validator checks them all, but pin to an address we have actually
    # verified is public (defence against a resolver returning mixed answers).
    if not resolved_ip:
        raise ValueError("Endpoint URL resolved to no usable address")
    return hostname, resolved_ip


def resolve_redirect_target(
    base_url: str,
    location: str,
    *,
    allow_private_network: bool = False,
) -> Tuple[str, str, str]:
    """Resolve and re-validate a redirect ``Location`` before following it.

    A redirect is a fresh SSRF boundary: a public endpoint can answer
    ``Location: http://169.254.169.254/latest/meta-data/`` and a client that
    follows it blindly reaches the cloud metadata service. Every hop is
    therefore run through the same gate as the original endpoint -- scheme
    allowlist, hostname/port checks, DNS resolution and private-IP rejection
    -- and returns the address to pin for the next request.

    Returns:
        ``(absolute_url, hostname, resolved_ip)`` for the redirect target.

    Raises:
        ValueError: When the Location is malformed or fails the SSRF gate.
    """
    if not location or len(location) > MAX_REDIRECT_LOCATION_LENGTH:
        raise ValueError("Redirect Location is missing or too long")
    target = urljoin(base_url, location)
    hostname, resolved_ip = validate_delivery_endpoint(
        target, allow_private_network=allow_private_network
    )
    return target, hostname, resolved_ip
