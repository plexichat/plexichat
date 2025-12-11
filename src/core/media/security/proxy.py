"""
External URL proxy for fetching, caching, and serving external content.
"""

import hashlib
import time
from typing import Optional, Tuple, BinaryIO
from urllib.parse import urlparse

import utils.logger as logger

from ..models import ProxiedContent
from ..exceptions import ProxyError, ProxyFetchError, ProxyCacheError


class ExternalProxy:
    """External URL proxy with caching."""

    ALLOWED_SCHEMES = {"http", "https"}

    DEFAULT_ALLOWED_TYPES = {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/svg+xml",
    }

    MAX_CONTENT_SIZE = 10 * 1024 * 1024

    def __init__(
        self,
        storage_backend,
        db,
        cache_path: str = "proxy_cache",
        cache_ttl: int = 86400,
        max_size: Optional[int] = None,
        allowed_types: Optional[set] = None,
        user_agent: str = "PlexiChat/1.0",
        timeout: int = 30,
    ):
        """
        Initialize external proxy.
        
        Args:
            storage_backend: Storage backend for cached content
            db: Database instance
            cache_path: Path prefix for cached files
            cache_ttl: Cache time-to-live in seconds
            max_size: Maximum content size to fetch
            allowed_types: Allowed content types
            user_agent: User agent for requests
            timeout: Request timeout in seconds
        """
        self._storage = storage_backend
        self._db = db
        self._cache_path = cache_path.strip("/")
        self._cache_ttl = cache_ttl
        self._max_size = max_size or self.MAX_CONTENT_SIZE
        self._allowed_types = allowed_types or self.DEFAULT_ALLOWED_TYPES
        self._user_agent = user_agent
        self._timeout = timeout

        try:
            import requests
            self._requests = requests
        except ImportError:
            raise ProxyError("requests is required for proxy. Install with: pip install requests")

    def fetch(self, url: str, force_refresh: bool = False) -> ProxiedContent:
        """
        Fetch and cache external URL.
        
        Args:
            url: URL to fetch
            force_refresh: Force refresh even if cached
            
        Returns:
            ProxiedContent object
        """
        self._validate_url(url)

        if not force_refresh:
            cached = self._get_cached(url)
            if cached:
                self._update_access(cached.id)
                return cached

        content_type, data = self._fetch_url(url)

        return self._cache_content(url, content_type, data)

    def get_content(self, url: str) -> Tuple[bytes, str]:
        """
        Get content for URL (from cache or fetch).
        
        Args:
            url: URL to get content for
            
        Returns:
            Tuple of (content bytes, content_type)
        """
        proxied = self.fetch(url)
        data = self._storage.retrieve(proxied.storage_path)
        return data, proxied.content_type

    def get_content_stream(self, url: str) -> Tuple[BinaryIO, int, str]:
        """
        Get content stream for URL.
        
        Args:
            url: URL to get content for
            
        Returns:
            Tuple of (stream, size, content_type)
        """
        proxied = self.fetch(url)
        stream, size = self._storage.retrieve_stream(proxied.storage_path)
        return stream, size, proxied.content_type

    def is_cached(self, url: str) -> bool:
        """Check if URL is cached and valid."""
        cached = self._get_cached(url)
        return cached is not None

    def invalidate(self, url: str) -> bool:
        """
        Invalidate cached content for URL.
        
        Args:
            url: URL to invalidate
            
        Returns:
            True if invalidated
        """
        row = self._db.fetch_one(
            "SELECT * FROM media_proxy_cache WHERE source_url = ?",
            (url,)
        )

        if not row:
            return False

        try:
            self._storage.delete(row["storage_path"])
        except Exception:
            pass

        self._db.execute(
            "DELETE FROM media_proxy_cache WHERE id = ?",
            (row["id"],)
        )

        return True

    def cleanup_expired(self) -> int:
        """
        Remove expired cache entries.
        
        Returns:
            Number of entries removed
        """
        now = int(time.time() * 1000)

        rows = self._db.fetch_all(
            "SELECT * FROM media_proxy_cache WHERE expires_at < ?",
            (now,)
        )

        count = 0
        for row in rows:
            try:
                self._storage.delete(row["storage_path"])
            except Exception:
                pass

            self._db.execute(
                "DELETE FROM media_proxy_cache WHERE id = ?",
                (row["id"],)
            )
            count += 1

        if count > 0:
            logger.debug(f"Cleaned up {count} expired proxy cache entries")

        return count

    def _validate_url(self, url: str):
        """
        Validate URL is allowed.
        
        Security checks:
        - Only HTTP/HTTPS schemes allowed
        - Block internal/private IP addresses (SSRF protection)
        - Block localhost and loopback addresses
        """
        parsed = urlparse(url)

        if parsed.scheme.lower() not in self.ALLOWED_SCHEMES:
            raise ProxyFetchError(f"Scheme not allowed: {parsed.scheme}", url)

        if not parsed.netloc:
            raise ProxyFetchError("Invalid URL: no host", url)

        # SSRF Protection: Block internal/private IP addresses
        hostname = parsed.hostname or ""
        hostname_lower = hostname.lower()

        # Block localhost variants
        blocked_hosts = {
            "localhost", "127.0.0.1", "::1", "0.0.0.0",
            "localhost.localdomain", "local"
        }
        if hostname_lower in blocked_hosts:
            raise ProxyFetchError("Access to localhost is not allowed", url)

        # Block internal hostnames
        if hostname_lower.endswith(".local") or hostname_lower.endswith(".internal"):
            raise ProxyFetchError("Access to internal hosts is not allowed", url)

        # Try to resolve and check for private IPs
        try:
            import socket
            # Get all IP addresses for the hostname
            addr_info = socket.getaddrinfo(hostname, None)
            for family, _, _, _, sockaddr in addr_info:
                ip = sockaddr[0]
                if self._is_private_ip(ip):
                    raise ProxyFetchError(f"Access to private IP addresses is not allowed", url)
        except socket.gaierror:
            # DNS resolution failed - allow through (will fail on fetch anyway)
            pass

    def _is_private_ip(self, ip: str) -> bool:
        """
        Check if IP address is private/internal.
        
        Blocks:
        - 10.0.0.0/8 (private)
        - 172.16.0.0/12 (private)
        - 192.168.0.0/16 (private)
        - 127.0.0.0/8 (loopback)
        - 169.254.0.0/16 (link-local)
        - ::1 (IPv6 loopback)
        - fc00::/7 (IPv6 private)
        - fe80::/10 (IPv6 link-local)
        """
        try:
            import ipaddress
            addr = ipaddress.ip_address(ip)
            return (
                addr.is_private or
                addr.is_loopback or
                addr.is_link_local or
                addr.is_reserved or
                addr.is_multicast
            )
        except ValueError:
            # Invalid IP format - block to be safe
            return True

    def _get_cached(self, url: str) -> Optional[ProxiedContent]:
        """Get cached content if valid."""
        now = int(time.time() * 1000)

        row = self._db.fetch_one(
            "SELECT * FROM media_proxy_cache WHERE source_url = ? AND expires_at > ?",
            (url, now)
        )

        if not row:
            return None

        if not self._storage.exists(row["storage_path"]):
            self._db.execute(
                "DELETE FROM media_proxy_cache WHERE id = ?",
                (row["id"],)
            )
            return None

        return ProxiedContent(
            id=row["id"],
            source_url=row["source_url"],
            content_type=row["content_type"],
            size=row["size"],
            storage_path=row["storage_path"],
            cached_at=row["cached_at"],
            expires_at=row["expires_at"],
            last_accessed=row["last_accessed"],
            access_count=row["access_count"],
            checksum=row["checksum"],
        )

    def _fetch_url(self, url: str) -> Tuple[str, bytes]:
        """Fetch content from URL."""
        try:
            response = self._requests.get(
                url,
                headers={"User-Agent": self._user_agent},
                timeout=self._timeout,
                stream=True,
            )

            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "application/octet-stream")
            content_type = content_type.split(";")[0].strip().lower()

            if content_type not in self._allowed_types:
                raise ProxyFetchError(f"Content type not allowed: {content_type}", url)

            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > self._max_size:
                raise ProxyFetchError(f"Content too large: {content_length} bytes", url)

            chunks = []
            total_size = 0

            for chunk in response.iter_content(chunk_size=8192):
                total_size += len(chunk)
                if total_size > self._max_size:
                    raise ProxyFetchError(f"Content too large: exceeded {self._max_size} bytes", url)
                chunks.append(chunk)

            return content_type, b"".join(chunks)
        except self._requests.RequestException as e:
            raise ProxyFetchError(f"Failed to fetch URL: {e}", url)

    def _cache_content(self, url: str, content_type: str, data: bytes) -> ProxiedContent:
        """Cache fetched content."""
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        ext = self._get_extension(content_type)
        storage_path = f"{self._cache_path}/{url_hash[:2]}/{url_hash}{ext}"

        checksum = hashlib.sha256(data).hexdigest()

        try:
            self._storage.store(data, storage_path, content_type)
        except Exception as e:
            raise ProxyCacheError(f"Failed to cache content: {e}", url)

        now = int(time.time() * 1000)
        expires_at = now + (self._cache_ttl * 1000)

        existing = self._db.fetch_one(
            "SELECT id FROM media_proxy_cache WHERE source_url = ?",
            (url,)
        )

        if existing:
            self._db.execute(
                """UPDATE media_proxy_cache SET
                   content_type = ?, size = ?, storage_path = ?, checksum = ?,
                   cached_at = ?, expires_at = ?, last_accessed = ?, access_count = access_count + 1
                   WHERE id = ?""",
                (content_type, len(data), storage_path, checksum, now, expires_at, now, existing["id"])
            )
            cache_id = existing["id"]
        else:
            from src.utils.encryption import generate_snowflake_id
            cache_id = generate_snowflake_id()

            self._db.execute(
                """INSERT INTO media_proxy_cache
                   (id, source_url, content_type, size, storage_path, checksum,
                    cached_at, expires_at, last_accessed, access_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (cache_id, url, content_type, len(data), storage_path, checksum, now, expires_at, now)
            )

        return ProxiedContent(
            id=cache_id,
            source_url=url,
            content_type=content_type,
            size=len(data),
            storage_path=storage_path,
            cached_at=now,
            expires_at=expires_at,
            last_accessed=now,
            access_count=1,
            checksum=checksum,
        )

    def _update_access(self, cache_id: int):
        """Update access timestamp and count."""
        now = int(time.time() * 1000)
        self._db.execute(
            "UPDATE media_proxy_cache SET last_accessed = ?, access_count = access_count + 1 WHERE id = ?",
            (now, cache_id)
        )

    def _get_extension(self, content_type: str) -> str:
        """Get file extension for content type."""
        extensions = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "image/svg+xml": ".svg",
        }
        return extensions.get(content_type, ".bin")
