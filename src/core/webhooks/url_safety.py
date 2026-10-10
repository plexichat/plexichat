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
from urllib.parse import urlparse

import utils.logger as logger

_ALLOWED_SCHEMES = {"http", "https"}


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
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"Endpoint scheme {scheme!r} not allowed; only http/https supported"
        )
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Invalid endpoint URL: missing hostname")

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
        # Test seam: still enforce the scheme allowlist and hostname
        # presence above, but permit loopback/private targets so the
        # end-to-end transport tests can bind a local HTTP server.
        return hostname, ""

    validator = URLValidator()
    return validator.validate_url_for_request(url)
