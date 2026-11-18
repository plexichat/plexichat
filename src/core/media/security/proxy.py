"""
External URL proxy for fetching, caching, and serving external content.
"""

import hashlib
import os
import time
import io
import importlib
from typing import Optional, Tuple, BinaryIO, Iterator
from urllib.parse import urlparse

try:
    requests = importlib.import_module("requests")
except Exception:
    requests = None

try:
    from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
except Exception:
    HTTPConnectionPool = None
    HTTPSConnectionPool = None

import utils.logger as logger
from src.utils.security import URLValidator, bracket_ip_literal

from ..models import ProxiedContent
from ..exceptions import ProxyError, ProxyFetchError, ProxyCacheError
from .._proxy_cache import ProxyContentCache


class ResponseStreamWrapper(io.RawIOBase):
    """
    Wrapper for urllib3 response stream that tracks size and calculates hash
    while reading, preventing large files from being fully loaded into memory.
    """

    def __init__(
        self, response_iterator: Iterator[bytes], max_size: int, initial: bytes = b""
    ):
        if len(initial) > max_size:
            raise ValueError(f"Content exceeds maximum size of {max_size} bytes")
        self._iterator = iter(response_iterator)
        self._max_size = max_size
        self._bytes_read = len(initial)
        self._hash = hashlib.sha256()
        self._hash.update(initial)
        self._buffer = initial
        # Upstream response metadata used solely to honour Cache-Control when
        # the caller decides whether to retain the bytes in the in-process
        # content cache. Populated by ``ExternalProxy._fetch_url``.
        self.cache_control: str = ""
        self.etag: str = ""

    def read(self, size: int = -1) -> bytes:
        if size == -1:
            # Not recommended for large streams, but implemented for completeness
            chunks = []
            while True:
                chunk = self.read(8192)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)

        while len(self._buffer) < size:
            try:
                chunk = next(self._iterator)
                if not chunk:
                    break

                self._bytes_read += len(chunk)
                if self._bytes_read > self._max_size:
                    raise ValueError(
                        f"Content exceeds maximum size of {self._max_size} bytes"
                    )

                self._hash.update(chunk)
                self._buffer += chunk
            except StopIteration:
                break

        res = self._buffer[:size]
        self._buffer = self._buffer[size:]
        return res

    def readable(self) -> bool:
        return True

    @property
    def bytes_read(self) -> int:
        return self._bytes_read

    @property
    def hex_digest(self) -> str:
        return self._hash.hexdigest()


class ExternalProxy:
    """External URL proxy with caching."""

    ALLOWED_SCHEMES = {"http", "https"}

    DEFAULT_ALLOWED_TYPES = {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
    }

    # SECURITY: image/svg+xml is deliberately NOT in DEFAULT_ALLOWED_TYPES.
    # SVG can carry embedded scripts; when a proxied SVG is opened directly
    # in a browser (not via <img>) those scripts execute.

    # Magic bytes used to verify that the streamed content matches the
    # declared Content-Type (and to sniff SVG masquerading as a raster
    # image). Content whose bytes contradict the declared type is rejected
    # before it is cached.
    IMAGE_MAGIC_BYTES = {
        "image/jpeg": (b"\xff\xd8\xff",),
        "image/png": (b"\x89PNG\r\n\x1a\n",),
        "image/gif": (b"GIF87a", b"GIF89a"),
        "image/webp": (b"RIFF",),  # full check: RIFF....WEBP
    }

    SVG_SNIFF_PREFIXES = (b"<svg", b"<?xml", b"<!doctype")

    # SECURITY: number of leading bytes accumulated for content sniffing.
    # Must be large enough that leading-whitespace/BOM padding cannot push
    # a "<svg"/"<?xml"/"<!doctype" marker out of the inspected window
    # (fail-closed: padded SVG polyglots are still rejected).
    SNIFF_WINDOW_SIZE = 4096

    MAX_CONTENT_SIZE = 10 * 1024 * 1024

    def __init__(
        self,
        storage_backend,
        db,
        cache_path: str = "proxy_cache",
        cache_ttl: int = 86400,
        max_size: Optional[int] = None,
        allowed_types: Optional[set] = None,
        user_agent: str = "Plexichat/1.0",
        timeout: int = 30,
        buffer_size: int = 65536,
        memory_cache_size: int = 256,
        memory_cache_max_bytes: int = 32 * 1024 * 1024,
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
            buffer_size: Buffer size for streaming
            memory_cache_size: Max entries in the in-process content cache
            memory_cache_max_bytes: Max total bytes retained in that cache
        """
        self._storage = storage_backend
        self._db = db
        self._cache_path = cache_path.strip("/")
        self._cache_ttl = cache_ttl
        self._max_size = max_size or self.MAX_CONTENT_SIZE
        self._allowed_types = allowed_types or self.DEFAULT_ALLOWED_TYPES
        self._user_agent = user_agent
        self._timeout = timeout
        self._buffer_size = buffer_size
        self._url_validator = URLValidator()

        # In-process bounded TTL cache of fully-validated content. This is the
        # hot path for repeated proxy requests: it avoids a second DNS lookup
        # and outbound fetch while keeping every security check on the miss
        # path (see ``get_content``). Both an entry cap and a byte cap apply.
        self._content_cache = ProxyContentCache(
            maxsize=memory_cache_size,
            max_bytes=memory_cache_max_bytes,
            ttl_seconds=cache_ttl,
        )

        if requests is None:
            raise ProxyError(
                "requests is required for proxy. Install with: pip install requests"
            )
        self._requests = requests

    def _assert_safe_url_structure(self, url: str) -> None:
        """Cheap, network-free SSRF gate re-run on *every* request.

        This mirrors the non-DNS portion of ``URLValidator`` (scheme, forbidden
        host literals, ``.local``/``.internal`` suffixes) so cache hits still
        pass an SSRF check. It performs no DNS resolution, so a cache hit does
        not pay the tens-of-milliseconds lookup that dominated proxy latency.
        The full validation — including DNS resolution to a pinned connect IP
        and the private/reserved-IP check — always runs on a cache miss before
        any outbound fetch.
        """
        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        if scheme not in self.ALLOWED_SCHEMES:
            raise ProxyFetchError(f"Scheme {scheme} not allowed", url)

        hostname = parsed.hostname
        if not hostname:
            raise ProxyFetchError("Invalid URL: missing hostname", url)

        hostname_lower = hostname.lower()
        # Loopback literals are allowed only under the self-test env flag,
        # exactly as in URLValidator.validate_url_for_request.
        selftest_loopback = os.getenv("PLEXICHAT_SELF_TEST") is not None and (
            hostname_lower in ("127.0.0.1", "localhost", "::1")
        )
        if not selftest_loopback:
            blocked = getattr(
                self._url_validator, "_blocked_hosts", URLValidator.BLOCKED_HOSTS
            )
            if hostname_lower in blocked:
                raise ProxyFetchError(f"Access to {hostname} is forbidden", url)
            if hostname_lower.endswith(".local") or hostname_lower.endswith(
                ".internal"
            ):
                raise ProxyFetchError(
                    f"Access to internal host {hostname} is forbidden", url
                )

    @staticmethod
    def _memory_cache_policy(cache_control: str) -> Tuple[bool, Optional[int]]:
        """Return ``(cacheable, ttl_override)`` from an upstream Cache-Control.

        ``no-store``, ``private`` and ``no-cache`` responses are not retained in
        the in-process cache. A smaller positive ``max-age`` shortens the TTL.
        """
        directives = [
            part.strip().lower()
            for part in (cache_control or "").split(",")
            if part.strip()
        ]
        for directive in ("no-store", "private", "no-cache"):
            if directive in directives:
                return False, None
        for directive in directives:
            if directive.startswith("max-age="):
                try:
                    return True, max(0, int(directive.split("=", 1)[1]))
                except ValueError:
                    pass
        return True, None

    def fetch(self, url: str, force_refresh: bool = False) -> ProxiedContent:
        """
        Fetch and cache external URL.

        Args:
            url: URL to fetch
            force_refresh: Force refresh even if cached

        Returns:
            ProxiedContent object
        """
        # SSRF Protection: Validate URL and resolve to IP to prevent DNS rebinding
        try:
            hostname, resolved_ip = self._url_validator.validate_url_for_request(url)
        except ValueError as e:
            raise ProxyFetchError(str(e), url)

        return self._fetch_validated(url, hostname, resolved_ip, force_refresh)

    def _fetch_validated(
        self,
        url: str,
        hostname: str,
        resolved_ip: str,
        force_refresh: bool = False,
    ) -> ProxiedContent:
        """Fetch using a destination already validated by ``fetch``/``get_content``."""
        if not force_refresh:
            cached = self._get_cached(url)
            if cached:
                self._update_access(cached.id)
                return cached

        content_type, stream_wrapper = self._fetch_url(url, hostname, resolved_ip)

        return self._cache_content(url, content_type, stream_wrapper)

    def get_content(self, url: str) -> Tuple[bytes, str]:
        """
        Get content for URL (from the in-process cache, DB cache, or fetch).

        The cheap structural SSRF gate runs on every request. A hit on the
        in-process cache is served without DNS or outbound I/O; a miss runs the
        full SSRF/DNS validation before any fetch, so a forbidden URL can never
        enter (or be served from) the cache.

        Args:
            url: URL to get content for

        Returns:
            Tuple of (content bytes, content_type)
        """
        # SSRF gate (network-free) — always runs, including on cache hits.
        self._assert_safe_url_structure(url)

        cached = self._content_cache.get(url)
        if cached is not None:
            return cached

        # Full SSRF validation (DNS resolution + private/reserved-IP check)
        # happens only on a miss, before any outbound fetch.
        try:
            hostname, resolved_ip = self._url_validator.validate_url_for_request(url)
        except ValueError as e:
            raise ProxyFetchError(str(e), url)

        proxied = self._fetch_validated(url, hostname, resolved_ip)
        data = self._storage.retrieve(proxied.storage_path)

        # Only fully-validated, size-capped content reaches this point. Honour
        # the upstream Cache-Control policy and cache the final bytes so the
        # next request needs neither DNS nor a fetch.
        cacheable, policy_ttl = self._memory_cache_policy(proxied.cache_control)
        if cacheable:
            effective_ttl = policy_ttl
            # A persistent-cache hit carries only its remaining lifetime (the
            # upstream directives are not persisted in the DB schema). Never
            # retain it in memory past the authoritative DB expiry.
            if proxied.remaining_ttl is not None:
                remaining = max(0, int(proxied.remaining_ttl))
                effective_ttl = (
                    remaining
                    if effective_ttl is None
                    else min(effective_ttl, remaining)
                )
            self._content_cache.put(
                url,
                (data, proxied.content_type),
                size=len(data),
                ttl_seconds=effective_ttl,
            )
        return data, proxied.content_type

    def content_cache_stats(self) -> dict:
        """Snapshot of the in-process content cache counters (observability)."""
        return self._content_cache.stats()

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
            "SELECT * FROM media_proxy_cache WHERE source_url = ?", (url,)
        )

        if not row:
            return False

        try:
            self._storage.delete(row["storage_path"])
        except Exception as exc:
            # The metadata row is dropped below either way; an orphaned object
            # is reclaimed by the storage sweep, so log and keep going.
            logger.debug(f"proxy cache blob delete failed: {exc}")

        self._db.execute("DELETE FROM media_proxy_cache WHERE id = ?", (row["id"],))
        # Keep the in-process cache consistent with the persistent one.
        self._content_cache.pop(url)

        return True

    def _validate_url(self, url: str) -> Tuple[str, str]:
        """
        Validate URL for proxying.
        Internal method used by tests.
        """
        try:
            return self._url_validator.validate_url_for_request(url)
        except ValueError as e:
            raise ProxyFetchError(str(e), url)

    def cleanup_expired(self) -> int:
        """
        Remove expired cache entries.

        Returns:
            Number of entries removed
        """
        now = int(time.time() * 1000)

        rows = self._db.fetch_all(
            "SELECT * FROM media_proxy_cache WHERE expires_at < ?", (now,)
        )

        count = 0
        for row in rows:
            try:
                self._storage.delete(row["storage_path"])
            except Exception as exc:
                logger.debug(f"proxy cache blob delete failed: {exc}")

            self._db.execute("DELETE FROM media_proxy_cache WHERE id = ?", (row["id"],))
            count += 1

        if count > 0:
            logger.debug(f"Cleaned up {count} expired proxy cache entries")

        return count

    def _get_cached(self, url: str) -> Optional[ProxiedContent]:
        """Get cached content if valid."""
        now = int(time.time() * 1000)

        row = self._db.fetch_one(
            "SELECT * FROM media_proxy_cache WHERE source_url = ? AND expires_at > ?",
            (url, now),
        )

        if not row:
            return None

        if not self._storage.exists(row["storage_path"]):
            self._db.execute("DELETE FROM media_proxy_cache WHERE id = ?", (row["id"],))
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
            remaining_ttl=max(0, int((row["expires_at"] - now) / 1000)),
        )

    def _fetch_url(
        self, url: str, hostname: str, resolved_ip: str
    ) -> Tuple[str, ResponseStreamWrapper]:
        """Fetch content from URL using safe IP.

        SECURITY: the connection is made to the ``resolved_ip`` (returned by
        the SSRF validator) so a DNS rebinding between validation and connect
        cannot redirect us to an internal host. The real ``hostname`` is
        carried in the ``Host`` header AND — for HTTPS — as the TLS
        ``server_hostname``/``assert_hostname`` so certificate verification
        and SNI still target the original host (verifying against the raw IP
        makes every HTTPS fetch fail). This mirrors what
        ``LinkPreviewService._fetch_with_redirects`` does with httpx's
        ``sni_hostname`` extension.
        """
        parsed = urlparse(url)
        try:
            port = parsed.port or (80 if parsed.scheme == "http" else 443)
        except ValueError as e:
            raise ProxyFetchError(f"Invalid port in URL: {e}", url)

        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        # ``urlparse().hostname`` strips the brackets from an IPv6 literal,
        # so put them back: RFC 7230 requires the Host header to carry the
        # bracketed form, otherwise an IPv6-literal source URL produces an
        # ambiguous header that the origin cannot route.
        host_header = bracket_ip_literal(hostname)
        if (parsed.scheme == "http" and port != 80) or (
            parsed.scheme == "https" and port != 443
        ):
            host_header = f"{host_header}:{port}"

        headers = {
            "User-Agent": self._user_agent,
            "Host": host_header,
        }
        timeout = self._timeout

        try:
            if parsed.scheme == "http":
                if HTTPConnectionPool is None:
                    raise ProxyFetchError("urllib3 is required for proxying", url)
                pool = HTTPConnectionPool(
                    resolved_ip,
                    port,
                    maxsize=1,
                    block=True,
                    headers=headers,
                    timeout=timeout,
                )
            elif parsed.scheme == "https":
                if HTTPSConnectionPool is None:
                    raise ProxyFetchError("urllib3 is required for proxying", url)
                pool = HTTPSConnectionPool(
                    resolved_ip,
                    port,
                    maxsize=1,
                    block=True,
                    headers=headers,
                    timeout=timeout,
                    cert_reqs="CERT_REQUIRED",
                    server_hostname=hostname,
                    assert_hostname=hostname,
                )
            else:
                raise ProxyFetchError(f"Unsupported scheme: {parsed.scheme}", url)

            response = pool.urlopen(
                "GET",
                path,
                headers=headers,
                timeout=timeout,
                retries=False,
                redirect=False,
                assert_same_host=False,
                preload_content=False,
                decode_content=True,
            )

            status_code = getattr(response, "status", 200)
            try:
                status_code = int(status_code)
            except Exception:
                status_code = 200

            if 300 <= status_code < 400:
                raise ProxyFetchError(
                    f"Redirects are not allowed for security reasons (status {status_code})",
                    url,
                )
            if status_code != 200:
                raise ProxyFetchError(f"Unexpected status code: {status_code}", url)

            content_type = response.headers.get(
                "Content-Type", "application/octet-stream"
            )
            content_type = content_type.split(";")[0].strip().lower()

            if content_type not in self._allowed_types:
                raise ProxyFetchError(f"Content type not allowed: {content_type}", url)

            # Retain upstream cache metadata so the in-process cache can honour
            # Cache-Control (no-store/private/no-cache and max-age) on hits.
            cache_control = response.headers.get("Cache-Control", "") or ""
            etag = response.headers.get("ETag", "") or ""

            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    content_length = int(content_length)
                except ValueError:
                    content_length = None
                if content_length is not None and content_length > self._max_size:
                    raise ProxyFetchError(
                        f"Content too large: {content_length} bytes", url
                    )

            # Sniff the first bytes of the body and validate them against the
            # declared type BEFORE anything is streamed into the cache. This
            # rejects SVG (script-bearing) content even when served under a
            # raster Content-Type, and rejects content whose magic bytes
            # contradict the declared type.
            gen = response.stream(amt=self._buffer_size, decode_content=True)
            peek, gen = self._sniff_content(gen, content_type, url)

            # Create stream wrapper for memory-safe reading and hashing
            stream_wrapper = ResponseStreamWrapper(gen, self._max_size, initial=peek)
            stream_wrapper.cache_control = cache_control
            stream_wrapper.etag = etag

            return content_type, stream_wrapper
        except ProxyFetchError:
            raise
        except Exception as e:
            raise ProxyFetchError(f"Failed to fetch URL: {e}", url)

    def _sniff_content(
        self, gen: Iterator[bytes], content_type: str, url: str
    ) -> Tuple[bytes, Iterator[bytes]]:
        """
        Read and validate the first bytes of a response stream.

        Returns:
            Tuple of (peek_bytes, remaining_stream_generator)

        Raises:
            ProxyFetchError: If the content looks like SVG or its magic bytes
                contradict the declared Content-Type.
        """
        peek = b""
        try:
            for chunk in gen:
                peek += chunk
                if len(peek) >= self.SNIFF_WINDOW_SIZE:
                    break
        except Exception as e:
            raise ProxyFetchError(f"Failed to read content: {e}", url)

        if not peek:
            return peek, gen

        stripped = peek.lstrip(b"\xef\xbb\xbf \t\r\n")
        head = stripped[:9].lower()
        if head.startswith(self.SVG_SNIFF_PREFIXES):
            raise ProxyFetchError("SVG content is blocked (can contain scripts)", url)

        if content_type in self.IMAGE_MAGIC_BYTES:
            expected = self.IMAGE_MAGIC_BYTES[content_type]
            if content_type == "image/webp":
                if not (
                    peek.startswith(b"RIFF")
                    and len(peek) >= 12
                    and peek[8:12] == b"WEBP"
                ):
                    raise ProxyFetchError(
                        f"Content does not match declared type {content_type}", url
                    )
            elif not any(peek.startswith(magic) for magic in expected):
                raise ProxyFetchError(
                    f"Content does not match declared type {content_type}", url
                )

        return peek, gen

    def _cache_content(
        self, url: str, content_type: str, stream_wrapper: ResponseStreamWrapper
    ) -> ProxiedContent:
        """Cache fetched content from stream."""
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        ext = self._get_extension(content_type)
        storage_path = f"{self._cache_path}/{url_hash[:2]}/{url_hash}{ext}"

        try:
            # store_stream will consume the wrapper, calculating hash and size
            self._storage.store_stream(stream_wrapper, storage_path, content_type, -1)
        except Exception as e:
            # SECURITY (fail-closed): a streaming failure part-way through
            # (e.g. the body exceeding the size cap) may leave a partial
            # object behind. Remove it best-effort so no attacker-controlled
            # truncated artifact lingers under the cache path.
            try:
                self._storage.delete(storage_path)
            except Exception as cleanup_error:
                # The ProxyCacheError below is the error the caller must see;
                # record the failed cleanup without masking it.
                logger.debug(
                    f"proxy cache cleanup failed for {storage_path!r}: {cleanup_error}"
                )
            raise ProxyCacheError(f"Failed to cache content: {e}", url)

        checksum = stream_wrapper.hex_digest
        size = stream_wrapper.bytes_read
        now = int(time.time() * 1000)
        expires_at = now + (self._cache_ttl * 1000)

        existing = self._db.fetch_one(
            "SELECT id FROM media_proxy_cache WHERE source_url = ?", (url,)
        )

        if existing:
            self._db.execute(
                """UPDATE media_proxy_cache SET
                   content_type = ?, size = ?, storage_path = ?, checksum = ?,
                   cached_at = ?, expires_at = ?, last_accessed = ?, access_count = access_count + 1
                   WHERE id = ?""",
                (
                    content_type,
                    size,
                    storage_path,
                    checksum,
                    now,
                    expires_at,
                    now,
                    existing["id"],
                ),
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
                (
                    cache_id,
                    url,
                    content_type,
                    size,
                    storage_path,
                    checksum,
                    now,
                    expires_at,
                    now,
                ),
            )

        return ProxiedContent(
            id=cache_id,
            source_url=url,
            content_type=content_type,
            size=size,
            storage_path=storage_path,
            cached_at=now,
            expires_at=expires_at,
            last_accessed=now,
            access_count=1,
            checksum=checksum,
            cache_control=getattr(stream_wrapper, "cache_control", "") or "",
            etag=getattr(stream_wrapper, "etag", "") or "",
        )

    def _update_access(self, cache_id: int):
        """Update access timestamp and count."""
        now = int(time.time() * 1000)
        self._db.execute(
            "UPDATE media_proxy_cache SET last_accessed = ?, access_count = access_count + 1 WHERE id = ?",
            (now, cache_id),
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
