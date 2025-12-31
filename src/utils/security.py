"""
Security utilities for the PlexiChat API.

Provides:
- SSRF protection (URL validation, IP blacklisting)
- DNS rebinding protection
- Content-Type verification
"""

import socket
import ipaddress
import utils.logger as logger
from typing import Tuple, Optional, Set
from urllib.parse import urlparse


class URLValidator:
    """Centralized URL validation for SSRF protection."""

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
            # We resolve here and the caller SHOULD use this IP to prevent DNS rebinding
            addr_info = socket.getaddrinfo(hostname, None)
            if not addr_info:
                raise ValueError(f"Could not resolve hostname: {hostname}")
                
            # Check ALL resolved IPs
            for _, _, _, _, sockaddr in addr_info:
                ip = str(sockaddr[0])
                if self.is_private_ip(ip):
                    raise ValueError(f"Hostname {hostname} resolves to forbidden IP {ip}")
            
            # Return the first resolved IP for the request
            resolved_ip = str(addr_info[0][4][0])
            return hostname, resolved_ip
            
        except socket.gaierror as e:
            logger.warning(f"DNS resolution failed for {hostname}: {e}")
            raise ValueError(f"DNS resolution failed for {hostname}")

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
