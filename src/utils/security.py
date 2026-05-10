"""
Security utilities for the Plexichat API.

Provides:
- SSRF protection (URL validation, IP blacklisting)
- DNS rebinding protection
- Content-Type verification
"""

import socket
import ipaddress
import secrets
import base64
import utils.logger as logger
from typing import Tuple, Optional, Set
from urllib.parse import urlparse, urlunparse


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
        "0.0.0.0",  # nosec B104
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
        # Replace hostname with resolved IP in the URL
        if parsed.port:
            netloc = f"{resolved_ip}:{parsed.port}"
        else:
            netloc = resolved_ip

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
            return (
                addr.is_private
                or addr.is_loopback
                or addr.is_link_local
                or addr.is_reserved
                or addr.is_multicast
                or addr.is_unspecified
            )
        except ValueError:
            # If not a valid IP, it might be a hostname that escaped previous checks
            return True
