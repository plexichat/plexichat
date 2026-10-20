"""
Security utilities for the Plexichat API.

Provides:
- SSRF protection (URL validation, IP blacklisting)
- DNS rebinding protection
- Content-Type verification
"""

import os
import socket
import ipaddress
import secrets
import base64
import utils.logger as logger
from typing import Any, Dict, Tuple, Optional, Set
from urllib.parse import urlparse, urlunparse


# RFC 6598 shared address space (CGNAT, 100.64.0.0/10): not flagged by
# ipaddress.is_private on some Python releases, but it is a non-global
# carrier-grade NAT range that must never be reachable via SSRF.
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")
_NAT64_NETWORK = ipaddress.ip_network("64:ff9b::/96")


def bracket_ip_literal(host: str) -> str:
    """Return ``host`` in the form a URL/``Host`` header requires.

    RFC 3986 section 3.2.2 requires IPv6 literals to carry square brackets
    inside a URL authority; a bare ``2606:4700::1111:8443`` is ambiguous
    with the port separator and no HTTP client parses it as intended. IPv4
    literals and DNS names pass through unchanged, and an already-bracketed
    host is not double-bracketed.
    """
    if ":" not in host:
        return host
    if host.startswith("["):
        return host
    return f"[{host}]"


def pinned_request_target(
    url: str, hostname: str, resolved_ip: str
) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    """Point ``url`` at the address ``URLValidator`` already cleared.

    Validating a URL and then requesting it by *name* lets the HTTP client
    resolve the host a second time, so a rebinding server can answer with a
    public address for the check and a loopback/private one for the
    connection. Callers that validate must therefore connect to the returned
    ``resolved_ip`` instead of the original host.

    The real hostname still has to be sent: RFC 7230 requires it in ``Host``
    (bracketed for IPv6, with the port only when it is not the scheme
    default) so virtual hosts route correctly, and HTTPS needs it as the TLS
    SNI / certificate hostname or every certificate check fails against a
    bare IP.

    Rebuilding the authority from parts also drops any ``user:pass@``
    userinfo embedded in the URL, so stored credentials are never re-sent.

    Args:
        url: The URL about to be requested.
        hostname: Its hostname, as returned by the validator.
        resolved_ip: The address the validator approved for it.

    Returns:
        ``(request_url, headers, extensions)``. Pass ``extensions`` to an
        httpx request as-is; it is empty when the URL is plain HTTP.

    Raises:
        ValueError: When the URL has no usable host or its port is invalid.
    """
    parsed = urlparse(url)
    if not parsed.hostname:
        raise ValueError(f"Invalid URL: {url!r} has no host")
    scheme = (parsed.scheme or "").lower()
    default_port = 80 if scheme == "http" else 443
    try:
        port = parsed.port or default_port
    except ValueError as exc:
        raise ValueError(f"invalid port in URL {url!r}: {exc}") from exc

    request_url = urlunparse(
        (
            parsed.scheme,
            f"{bracket_ip_literal(resolved_ip)}:{port}",
            parsed.path or "/",
            parsed.params,
            parsed.query,
            "",
        )
    )

    host_header = bracket_ip_literal(hostname)
    if port != default_port:
        host_header = f"{host_header}:{port}"

    headers = {"Host": host_header}
    extensions: Dict[str, Any] = {}
    if scheme == "https":
        extensions["sni_hostname"] = hostname
    return request_url, headers, extensions


def generate_csp_nonce() -> str:
    """
    Generate a cryptographically secure nonce for CSP.

    Returns:
        Base64-encoded 16-byte random nonce
    """
    return base64.b64encode(secrets.token_bytes(16)).decode("utf-8")


def build_admin_csp_header(nonce: str) -> str:
    """
    Build Content-Security-Policy header for the Admin UI.

    Args:
        nonce: CSP nonce for inline scripts

    Returns:
        CSP header value string
    """
    return (
        f"default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net; "
        f"style-src 'self' 'unsafe-inline'; "
        f"img-src 'self' data:; "
        f"connect-src 'self'; "
        f"frame-ancestors 'none'; "
        f"base-uri 'self'; "
        f"form-action 'self';"
    )


class URLValidator:
    """
    Centralized URL validation for SSRF protection.

    Returns both the original hostname and resolved IP. Callers MUST use
    the resolved IP for the actual connection to prevent DNS rebinding attacks.
    """

    BLOCKED_HOSTS = {
        "localhost",
        "127.0.0.1",
        "::1",
        "0.0.0.0",
        "localhost.localdomain",
        "local",
    }

    def __init__(self, blocked_hosts: Optional[Set[str]] = None):
        self._blocked_hosts = blocked_hosts or self.BLOCKED_HOSTS

    def validate_url_for_request(self, url: str) -> Tuple[str, str]:
        """
        Validate URL and resolve to a safe IP address.

        Returns:
            Tuple of (original_hostname, resolved_ip)

        IMPORTANT: Callers MUST use the resolved_ip for the actual HTTP connection
        and set the Host header to original_hostname. This prevents DNS rebinding.

        Raises:
            ValueError: If URL is invalid or points to a forbidden location.
        """
        parsed = urlparse(url)

        scheme = (parsed.scheme or "").lower()
        if scheme not in {"http", "https"}:
            raise ValueError(f"Scheme {scheme} not allowed. Only http/https supported.")

        hostname = parsed.hostname
        if not hostname:
            raise ValueError("Invalid URL: missing hostname")

        hostname_lower = hostname.lower()

        # Self-test allowance (TEST ONLY): when the PLEXICHAT_SELF_TEST
        # environment variable is set (self-test CLI mode), loopback
        # literals resolve straight to 127.0.0.1/::1 without DNS. This lets
        # the suite exercise outbound federation delivery against a fake
        # remote instance running alongside the server. Scope is deliberately
        # minimal: loopback literals only, env-gated, no effect on any other
        # host. Without the env var every loopback URL below stays blocked.
        if os.getenv("PLEXICHAT_SELF_TEST") is not None:
            if hostname_lower in ("127.0.0.1", "localhost"):
                return hostname, "127.0.0.1"
            if hostname_lower == "::1":
                return hostname, "::1"

        # 1. Block known forbidden hostnames
        if hostname_lower in self._blocked_hosts:
            raise ValueError(f"Access to {hostname} is forbidden")

        if hostname_lower.endswith(".local") or hostname_lower.endswith(".internal"):
            raise ValueError(f"Access to internal host {hostname} is forbidden")

        # 2. Resolve DNS and check IP
        try:
            # Resolve DNS once and use this IP for the request
            addr_info = socket.getaddrinfo(hostname, None)
            if not addr_info:
                raise ValueError(f"Could not resolve hostname: {hostname}")

            # Check ALL resolved IPs for safety
            for _, _, _, _, sockaddr in addr_info:
                ip = str(sockaddr[0])
                if self.is_private_ip(ip):
                    raise ValueError(
                        f"Hostname {hostname} resolves to forbidden IP {ip}"
                    )

            # Return the first resolved IP for the request
            resolved_ip = str(addr_info[0][4][0])
            return hostname, resolved_ip

        except socket.gaierror as e:
            logger.warning(f"DNS resolution failed for {hostname}: {e}")
            raise ValueError(f"DNS resolution failed for {hostname}")

    def get_safe_url(self, url: str) -> Tuple[str, str, str]:
        """
        Validate URL and return a safe URL with resolved IP.

        Returns:
            Tuple of (safe_url_with_ip, original_hostname, resolved_ip)

        The safe_url_with_ip has the hostname replaced with the resolved IP.
        Use original_hostname in the Host header.
        """
        hostname, resolved_ip = self.validate_url_for_request(url)

        parsed = urlparse(url)
        # Replace hostname with resolved IP in the URL. An IPv6 literal has
        # to be bracketed or the reconstructed authority is ambiguous with
        # the ``:port`` separator and no client parses it as intended.
        netloc = bracket_ip_literal(resolved_ip)
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"

        safe_url = urlunparse(
            (
                parsed.scheme,
                netloc,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )

        return safe_url, hostname, resolved_ip

    def is_private_ip(self, ip: str) -> bool:
        """Check if an IP address is private or reserved."""
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            # If not a valid IP, it might be a hostname that escaped previous checks
            return True
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            return True
        # Explicit CGNAT check — covers plain IPv4 and IPv4-mapped IPv6
        # (``::ffff:100.64.0.1`` has ``version == 6`` and ``is_private`` is
        # False for the embedded address on some Python releases).
        if addr.version == 4:
            return addr in _CGNAT_NETWORK
        if isinstance(addr, ipaddress.IPv6Address):
            # NAT64 transition range (64:ff9b::/96) embeds an IPv4 address in
            # the low 32 bits; on NAT64-capable hosts it routes to that IPv4
            # address, so private targets must be rejected.
            if addr in _NAT64_NETWORK:
                embedded = ipaddress.ip_address(int.from_bytes(addr.packed[12:], "big"))
                if self.is_private_ip(str(embedded)):
                    return True
            if addr.ipv4_mapped is not None:
                if self.is_private_ip(str(addr.ipv4_mapped)):
                    return True
                return addr.ipv4_mapped in _CGNAT_NETWORK
        return False
