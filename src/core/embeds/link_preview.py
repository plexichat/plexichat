"""
Secure link preview generation.

Provides safe URL preview with:
- SSRF protection (private IP blocking, DNS rebinding prevention)
- Rate limiting per user
- Preview caching
- Image proxying (prevents IP leakage, validates content)
- SVG sanitization
- Redirect chain validation
"""

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable, Optional, Dict, Any, List, Tuple
from urllib.parse import quote, urlparse, urljoin

import utils.logger as logger
import utils.config as config
from src.utils.security import URLValidator, bracket_ip_literal


# Image magic bytes for validation
IMAGE_MAGIC_BYTES = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
    b"RIFF": "image/webp",  # Check for WEBP at offset 8
}

# Blocked image types (security risk)
BLOCKED_IMAGE_TYPES = {"image/svg+xml"}  # SVG can contain scripts

# Default configuration
DEFAULT_CONFIG = {
    "enabled": True,
    "timeout_seconds": 8,
    "max_html_bytes": 512 * 1024,  # 512KB (kept for back-compat)
    "max_body_size": 512 * 1024,  # 512KB (streamed read cap)
    "max_redirects": 5,
    "max_image_size": 5 * 1024 * 1024,  # 5MB
    "cache_ttl_seconds": 3600,  # 1 hour (persistent DB cache)
    # Short-TTL in-process tier in front of the expensive metadata fetch.
    # Bounded by both entry count and estimated payload bytes so a stream of
    # distinct URLs cannot pin unbounded memory in a long-lived worker.
    "resolution_cache_ttl_seconds": 15,
    "resolution_cache_max_entries": 256,
    "resolution_cache_max_bytes": 512 * 1024,
    "rate_limit_requests": 10,
    "rate_limit_window_seconds": 60,
    "proxy_images": True,
    "allowed_schemes": ["http", "https"],
    "user_agent": "Plexichat/1.0 LinkPreview (+https://plexichat)",
}


@dataclass
class PreviewMetadata:
    """Parsed URL preview metadata."""

    url: str
    title: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    site_name: Optional[str] = None
    embed_type: str = "link"
    author: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "description": self.description,
            "image_url": self.image_url,
            "site_name": self.site_name,
            "type": self.embed_type,
            "author": self.author,
        }


@dataclass
class CachedPreview:
    """Cached preview data."""

    metadata: PreviewMetadata
    proxied_image_id: Optional[int]
    expires_at: int


def _clone_preview_metadata(metadata: PreviewMetadata) -> PreviewMetadata:
    """Return a detached copy so no caller can mutate a shared cache entry."""
    return PreviewMetadata(
        url=metadata.url,
        title=metadata.title,
        description=metadata.description,
        image_url=metadata.image_url,
        site_name=metadata.site_name,
        embed_type=metadata.embed_type,
        author=metadata.author,
    )


@dataclass
class _ResolutionEntry:
    """One resolved preview held in the short-TTL tier."""

    metadata: PreviewMetadata
    proxied_image_id: Optional[int]
    expires_at: float
    size: int


@dataclass
class _PreviewFlight:
    """One shared outcome for concurrent identical URL resolutions."""

    event: threading.Event
    metadata: Optional[PreviewMetadata] = None
    error: Optional[BaseException] = None


class ResolutionCache:
    """Thread-safe, bounded, short-TTL cache of resolved preview metadata.

    ``LinkPreviewService`` still runs the full SSRF/scheme gate on *every*
    request before consulting this cache. The cache only avoids repeating the
    expensive remote metadata fetch, parse and image-proxy work for a URL that
    has just been validated on this request.

    Keys are the full normalized URL (scheme/host lower-cased, port preserved,
    path/query/fragment kept verbatim), so two resources differing only in
    path or query case cannot share an entry. Bounded by entry count *and*
    estimated UTF-8 payload bytes; the least-recently-used entry is evicted
    first. A single entry larger than the byte cap is simply not stored.
    """

    def __init__(
        self,
        ttl_seconds: float,
        max_entries: int,
        max_bytes: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = max(0.0, float(ttl_seconds))
        self._max_entries = max(1, int(max_entries))
        self._max_bytes = max(1, int(max_bytes))
        self._clock = clock
        self._entries: "OrderedDict[str, _ResolutionEntry]" = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._expirations = 0
        self._evictions = 0

    @staticmethod
    def _entry_size(key: str, metadata: PreviewMetadata) -> int:
        """Estimate retained bytes, including the full normalized URL key."""
        total = 128 + len(key.encode("utf-8"))  # dataclass + key allowance
        for value in (
            metadata.url,
            metadata.title,
            metadata.description,
            metadata.image_url,
            metadata.site_name,
            metadata.embed_type,
            metadata.author,
        ):
            if isinstance(value, str):
                total += len(value.encode("utf-8"))
        return total

    def get(self, key: str) -> Optional[_ResolutionEntry]:
        """Return a detached entry on a live hit, None on miss or expiry."""
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            if entry.expires_at <= now:
                # Drop expired keys so stale entries cannot accumulate, and
                # never serve them even if the clock has not advanced much.
                del self._entries[key]
                self._bytes -= entry.size
                self._expirations += 1
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return _ResolutionEntry(
                metadata=_clone_preview_metadata(entry.metadata),
                proxied_image_id=entry.proxied_image_id,
                expires_at=entry.expires_at,
                size=entry.size,
            )

    def put(
        self,
        key: str,
        metadata: PreviewMetadata,
        proxied_image_id: Optional[int],
    ) -> None:
        """Store a detached copy of a successful resolution."""
        stored = _clone_preview_metadata(metadata)
        size = self._entry_size(key, stored)
        expires_at = self._clock() + self._ttl
        with self._lock:
            old = self._entries.pop(key, None)
            if old is not None:
                self._bytes -= old.size
            if size > self._max_bytes:
                # Never retain a single payload that alone busts the byte cap.
                return
            while self._entries and (
                len(self._entries) >= self._max_entries
                or self._bytes + size > self._max_bytes
            ):
                _, evicted = self._entries.popitem(last=False)
                self._bytes -= evicted.size
                self._evictions += 1
            self._entries[key] = _ResolutionEntry(
                metadata=stored,
                proxied_image_id=proxied_image_id,
                expires_at=expires_at,
                size=size,
            )
            self._bytes += size

    def clear(self) -> None:
        """Drop every cached resolution."""
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def stats(self) -> Dict[str, Any]:
        """Snapshot of cache counters and current bounds."""
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "expirations": self._expirations,
                "evictions": self._evictions,
                "size": len(self._entries),
                "bytes": self._bytes,
                "max_entries": self._max_entries,
                "max_bytes": self._max_bytes,
                "ttl_seconds": self._ttl,
            }


class MetaTagParser(HTMLParser):
    """HTML parser for extracting OpenGraph and Twitter Card metadata."""

    def __init__(self):
        super().__init__()
        self.meta: Dict[str, str] = {}
        self.title: Optional[str] = None
        self._in_title = False
        self._title_content: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag == "title":
            self._in_title = True
            return

        if tag != "meta":
            return

        attrs_dict = {k: v for k, v in attrs if v is not None}

        # OpenGraph tags
        prop = attrs_dict.get("property", "")
        if prop.startswith("og:") or prop.startswith("article:"):
            content = attrs_dict.get("content", "")
            if content:
                self.meta[prop] = content

        # Twitter Card tags
        name = attrs_dict.get("name", "")
        if name.startswith("twitter:") or name in ("description", "author"):
            content = attrs_dict.get("content", "")
            if content:
                self.meta[name] = content

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in_title:
            self._in_title = False
            self.title = "".join(self._title_content).strip()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_content.append(data)


class LinkPreviewService:
    """
    Secure link preview service.

    Handles URL fetching, metadata extraction, image proxying,
    caching, and rate limiting with comprehensive security measures.
    """

    def __init__(self, db, media_proxy=None):
        """
        Initialize link preview service.

        Args:
            db: Database instance
            media_proxy: Optional media proxy for image caching
        """
        self._db = db
        self._media_proxy = media_proxy
        self._url_validator = URLValidator()
        self._config = self._load_config()
        self._http_client = None
        self._resolution_cache = ResolutionCache(
            ttl_seconds=self._config["resolution_cache_ttl_seconds"],
            max_entries=self._config["resolution_cache_max_entries"],
            max_bytes=self._config["resolution_cache_max_bytes"],
        )

        # Bounded in-flight registry for concurrent identical resolutions. The
        # resolution cache absorbs *sequential* repeats, but a burst of requests
        # for the same cold URL used to each pay for a full remote fetch + parse
        # + image proxy. One leader resolves; the rest wait and share its result.
        # The key is the normalized URL (never a user id): every caller has
        # already cleared the per-request SSRF gate and rate limiter for that
        # same URL, and results are handed out detached, so no user can observe
        # or poison another's resolution. The cap bounds waiter state.
        self._flights: Dict[str, _PreviewFlight] = {}
        self._flights_lock = threading.Lock()
        self._max_inflight_resolutions = 64
        self._flight_stats = {"led": 0, "joined": 0, "rejected": 0, "peak": 0}

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration with defaults."""
        user_config = config.get("embeds", {}).get("url_preview", {})
        return {**DEFAULT_CONFIG, **user_config}

    def _get_http_client(self):
        """Get or create HTTP client."""
        if self._http_client is None:
            try:
                import httpx

                self._http_client = httpx.Client(
                    follow_redirects=False,  # We handle redirects manually for security
                    timeout=httpx.Timeout(
                        self._config["timeout_seconds"],
                        connect=min(5, self._config["timeout_seconds"]),
                    ),
                    limits=httpx.Limits(
                        max_connections=10, max_keepalive_connections=5
                    ),
                )
            except ImportError:
                raise RuntimeError("httpx is required for link previews")
        return self._http_client

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Canonicalize the full URL for use as a cache key.

        Only the scheme and host are case-insensitive under RFC 3986. Path,
        query and fragment are preserved verbatim so two resources differing
        only in case cannot collide. IPv6 literals keep their brackets in the
        authority so the key stays an unambiguous, full URL.
        """
        parsed = urlparse(url)
        host = bracket_ip_literal((parsed.hostname or "").lower())
        port = f":{parsed.port}" if parsed.port else ""
        normalized = f"{parsed.scheme.lower()}://{host}{port}{parsed.path}"
        if parsed.query:
            normalized += f"?{parsed.query}"
        if parsed.fragment:
            normalized += f"#{parsed.fragment}"
        return normalized

    @classmethod
    def _hash_url(cls, url: str) -> str:
        """Generate the persistent cache key hash for a URL.

        Only the parts that RFC 3986 defines as case-insensitive -- the
        scheme and the host -- are lowercased. The previous implementation
        lowercased the WHOLE URL, so ``https://host/ABCDEF`` and
        ``https://host/abcdef`` collided on one ``url_hash``: previewing one
        returned the other's cached metadata (including the other's ``url``),
        a stored content-spoofing primitive on any host with case-sensitive
        paths. Path, query and fragment are preserved exactly.
        """
        return hashlib.sha256(cls._normalize_url(url).encode("utf-8")).hexdigest()[:32]

    def _check_rate_limit(self, user_id: int) -> bool:
        """
        Check if user is within rate limits.

        Returns:
            True if request is allowed, False if rate limited
        """
        now = int(time.time())
        window_seconds = self._config["rate_limit_window_seconds"]
        window_start = now - (now % window_seconds)

        row = self._db.fetch_one(
            """SELECT request_count FROM embed_preview_rate_limits
               WHERE user_id = ? AND window_start = ?""",
            (user_id, window_start),
        )

        current_count = row["request_count"] if row else 0
        max_requests = self._config["rate_limit_requests"]

        return current_count < max_requests

    def _update_rate_limit(self, user_id: int) -> None:
        """Update rate limit counter."""
        now = int(time.time())
        window_seconds = self._config["rate_limit_window_seconds"]
        window_start = now - (now % window_seconds)

        self._db.execute(
            """INSERT INTO embed_preview_rate_limits (user_id, window_start, request_count)
               VALUES (?, ?, 1)
               ON CONFLICT(user_id, window_start) DO UPDATE SET
               request_count = request_count + 1""",
            (user_id, window_start),
        )

        # Cleanup old entries
        cutoff = now - (window_seconds * 10)
        self._db.execute(
            "DELETE FROM embed_preview_rate_limits WHERE window_start < ?", (cutoff,)
        )

    def _consume_rate_limit(self, user_id: int) -> bool:
        """
        Atomically consume one unit of the per-user rate-limit budget.

        Uses a single conditional upsert so concurrent requests cannot both
        pass a check-then-act race and together exceed the configured limit.

        Returns:
            True if the request is allowed, False if rate limited.
        """
        now = int(time.time())
        window_seconds = self._config["rate_limit_window_seconds"]
        window_start = now - (now % window_seconds)
        max_requests = self._config["rate_limit_requests"]

        result = self._db.execute(
            """INSERT INTO embed_preview_rate_limits (user_id, window_start, request_count)
               VALUES (?, ?, 1)
               ON CONFLICT(user_id, window_start) DO UPDATE SET
               request_count = request_count + 1
               WHERE request_count < ?""",
            (user_id, window_start, max_requests),
        )

        rowcount = getattr(result, "rowcount", None)
        if rowcount is None:
            # Drivers without a reliable rowcount: re-read the counter.
            row = self._db.fetch_one(
                """SELECT request_count FROM embed_preview_rate_limits
                   WHERE user_id = ? AND window_start = ?""",
                (user_id, window_start),
            )
            # The conditional upsert refuses increments at the ceiling, so
            # a re-read at the ceiling cannot prove this request consumed
            # a unit; grant only strictly below it (no fail-open past the cap).
            allowed = bool(row) and row["request_count"] < max_requests
        else:
            allowed = int(rowcount) > 0

        # Cleanup old entries
        cutoff = now - (window_seconds * 10)
        self._db.execute(
            "DELETE FROM embed_preview_rate_limits WHERE window_start < ?", (cutoff,)
        )

        return allowed

    def _get_cached_preview(self, url: str) -> Optional[CachedPreview]:
        """Get cached preview if valid."""
        url_hash = self._hash_url(url)
        now = int(time.time() * 1000)

        row = self._db.fetch_one(
            """SELECT * FROM embed_preview_cache
               WHERE url_hash = ? AND expires_at > ?""",
            (url_hash, now),
        )

        if not row:
            return None

        # Update fetch count
        self._db.execute(
            "UPDATE embed_preview_cache SET fetch_count = fetch_count + 1 WHERE id = ?",
            (row["id"],),
        )

        metadata = PreviewMetadata(
            url=row["url"],
            title=row["title"],
            description=row["description"],
            image_url=row["image_url"],
            site_name=row["site_name"],
            embed_type=row["embed_type"],
        )

        return CachedPreview(
            metadata=metadata,
            proxied_image_id=row["proxied_image_id"],
            expires_at=row["expires_at"],
        )

    def _cache_preview(
        self,
        url: str,
        metadata: PreviewMetadata,
        proxied_image_id: Optional[int] = None,
    ) -> None:
        """Cache preview metadata."""
        from src.utils.encryption import generate_snowflake_id

        url_hash = self._hash_url(url)
        now = int(time.time() * 1000)
        ttl_ms = self._config["cache_ttl_seconds"] * 1000
        expires_at = now + ttl_ms

        self._db.execute(
            """INSERT INTO embed_preview_cache 
               (id, url_hash, url, title, description, image_url, proxied_image_id,
                site_name, embed_type, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(url_hash) DO UPDATE SET
               title = excluded.title,
               description = excluded.description,
               image_url = excluded.image_url,
               proxied_image_id = excluded.proxied_image_id,
               site_name = excluded.site_name,
               embed_type = excluded.embed_type,
               expires_at = excluded.expires_at,
               fetch_count = fetch_count + 1""",
            (
                generate_snowflake_id(),  # Uniqueness is guaranteed by the generator
                url_hash,
                url,
                metadata.title,
                metadata.description,
                metadata.image_url,
                proxied_image_id,
                metadata.site_name,
                metadata.embed_type,
                now,
                expires_at,
            ),
        )

    def _validate_url(self, url: str) -> Tuple[str, str, str]:
        """
        Validate URL for security.

        Returns:
            Tuple of (hostname, resolved_ip, validated_url)

        Raises:
            ValueError: If URL is invalid or blocked
        """
        parsed = urlparse(url)

        # Check scheme
        if parsed.scheme.lower() not in self._config["allowed_schemes"]:
            raise ValueError(f"Scheme not allowed: {parsed.scheme}")

        # Use URLValidator for SSRF protection
        hostname, resolved_ip = self._url_validator.validate_url_for_request(url)

        return hostname, resolved_ip, url

    def _validate_redirect(
        self, redirect_url: str, original_hostname: str
    ) -> Tuple[str, str]:
        """
        Validate redirect URL for security.

        Prevents SSRF via redirect to internal IPs.

        Returns:
            Tuple of (hostname, resolved_ip)
        """
        parsed = urlparse(redirect_url)

        if parsed.scheme.lower() not in self._config["allowed_schemes"]:
            raise ValueError(f"Redirect to blocked scheme: {parsed.scheme}")

        # Validate the redirect destination
        return self._url_validator.validate_url_for_request(redirect_url)

    @staticmethod
    def _read_limited(response: Any, max_size: int) -> bytes:
        """
        Read a streaming response body up to ``max_size`` bytes.

        Stops reading as soon as the cap is exceeded so a malicious
        remote server cannot force us to buffer an unbounded body in
        memory (the previous implementation materialised the whole body
        with ``response.content`` before slicing it).

        Raises:
            ValueError: If the body exceeds ``max_size`` bytes.
        """
        chunks = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > max_size:
                raise ValueError(f"Response exceeds maximum size of {max_size} bytes")
            chunks.append(chunk)
        return b"".join(chunks)

    def _fetch_with_redirects(
        self,
        url: str,
        hostname: Optional[str] = None,
        resolved_ip: Optional[str] = None,
    ) -> Tuple[bytes, str, str]:
        """
        Fetch URL with secure redirect handling.

        Args:
            url: URL to fetch
            hostname: Real hostname (used for the ``Host`` header and TLS
                SNI). Defaults to the URL's hostname when omitted.
            resolved_ip: IP resolved by the SSRF validator. Defaults to the
                hostname when omitted (production always passes the
                validator-resolved IP from ``generate_preview``).

        Returns:
            Tuple of (content, final_url, content_type)
        """
        parsed = urlparse(url)
        if hostname is None:
            hostname = parsed.hostname or parsed.netloc
        if resolved_ip is None:
            resolved_ip = hostname

        client = self._get_http_client()
        max_redirects = self._config["max_redirects"]
        max_size = self._config["max_body_size"]

        current_url = url
        current_hostname = hostname
        current_ip = resolved_ip

        for redirect_count in range(max_redirects + 1):
            # Build request URL using resolved IP
            parsed = urlparse(current_url)
            try:
                port = parsed.port or (80 if parsed.scheme == "http" else 443)
            except ValueError as e:
                raise ValueError(f"Invalid port in URL: {e}")
            # An IPv6 resolved IP must be bracketed inside the authority,
            # otherwise the address and the ":port" suffix merge into one
            # ambiguous token and the request goes nowhere.
            request_url = (
                f"{parsed.scheme}://{bracket_ip_literal(current_ip)}"
                f":{port}{parsed.path or '/'}"
            )
            if parsed.query:
                request_url += f"?{parsed.query}"

            host_header = current_hostname
            if (parsed.scheme == "http" and port != 80) or (
                parsed.scheme == "https" and port != 443
            ):
                host_header = f"{bracket_ip_literal(current_hostname)}:{port}"

            headers = {
                "User-Agent": self._config["user_agent"],
                "Accept": "text/html,application/xhtml+xml",
                "Host": host_header,
            }

            # SECURITY: we connect to the resolved IP (DNS-rebinding
            # protection) but the TLS SNI / certificate hostname must
            # still be the real hostname or HTTPS certificate
            # verification fails for every normal site.
            extensions = {}
            if parsed.scheme == "https":
                extensions["sni_hostname"] = current_hostname

            try:
                with client.stream(
                    "GET",
                    request_url,
                    headers=headers,
                    follow_redirects=False,
                    extensions=extensions,
                ) as response:
                    # Handle redirects
                    if response.status_code in (301, 302, 303, 307, 308):
                        if redirect_count >= max_redirects:
                            raise ValueError(
                                f"Too many redirects (max {max_redirects})"
                            )

                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("Redirect without Location header")

                        # Resolve relative redirects
                        redirect_url = urljoin(current_url, location)

                        # Validate redirect destination (SSRF protection)
                        try:
                            current_hostname, current_ip = self._validate_redirect(
                                redirect_url, current_hostname
                            )
                        except ValueError as e:
                            raise ValueError(f"Blocked redirect: {e}")

                        current_url = redirect_url
                        logger.debug(f"Following redirect to: {redirect_url}")
                        continue

                    # Check response status
                    if response.status_code != 200:
                        raise ValueError(f"HTTP {response.status_code}")

                    # Check content type
                    content_type = response.headers.get("content-type", "").lower()
                    if (
                        "text/html" not in content_type
                        and "application/xhtml+xml" not in content_type
                    ):
                        raise ValueError(f"Unsupported content type: {content_type}")

                    # Read content with size limit (streamed)
                    content = self._read_limited(response, max_size)

                    return content, current_url, content_type
            except ValueError:
                raise
            except Exception as e:
                raise ValueError(f"Failed to fetch URL: {e}")

        raise ValueError("Redirect loop detected")

    def _parse_metadata(self, html: bytes, base_url: str) -> PreviewMetadata:
        """Parse HTML for OpenGraph/Twitter Card metadata."""
        try:
            html_text = html.decode("utf-8", errors="replace")
        except Exception:
            html_text = html.decode("latin-1", errors="replace")

        parser = MetaTagParser()
        try:
            parser.feed(html_text)
        except Exception as e:
            logger.warning(f"HTML parsing error: {e}")

        parsed = urlparse(base_url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        def pick(*keys: str) -> Optional[str]:
            for k in keys:
                v = parser.meta.get(k)
                if v:
                    return v.strip()
            return None

        # Get image URL and resolve relative paths
        image_url = pick(
            "og:image:secure_url",
            "og:image:url",
            "og:image",
            "twitter:image",
            "twitter:image:src",
        )
        if image_url:
            image_url = urljoin(base + "/", image_url)

        # Determine embed type
        og_type = pick("og:type") or ""
        embed_type = "link"
        if og_type.lower().startswith("video"):
            embed_type = "video"
        elif og_type.lower() == "article":
            embed_type = "article"

        return PreviewMetadata(
            url=base_url,
            title=pick("og:title", "twitter:title") or parser.title,
            description=pick("og:description", "twitter:description", "description"),
            image_url=image_url,
            site_name=pick("og:site_name") or parsed.netloc,
            embed_type=embed_type,
            author=pick("author", "article:author"),
        )

    def _validate_image_url(self, image_url: str) -> Tuple[str, str]:
        """
        Validate image URL for security.

        Returns:
            Tuple of (hostname, resolved_ip)
        """
        return self._url_validator.validate_url_for_request(image_url)

    def _validate_image_content(self, data: bytes, content_type: str) -> bool:
        """
        Validate image content using magic bytes.

        Returns:
            True if valid image, False otherwise
        """
        # Block SVG (can contain scripts)
        if content_type.lower() in BLOCKED_IMAGE_TYPES:
            logger.warning(f"Blocked image type: {content_type}")
            return False

        # Check magic bytes
        for magic, mime in IMAGE_MAGIC_BYTES.items():
            if data.startswith(magic):
                # Special case for WebP (RIFF....WEBP)
                if magic == b"RIFF":
                    if len(data) >= 12 and data[8:12] == b"WEBP":
                        return True
                    continue
                return True

        logger.warning("Image magic byte validation failed")
        return False

    def _proxy_image(self, image_url: str) -> Optional[int]:
        """
        Proxy and cache image through our server.

        Delegates the fetch entirely to the configured media proxy, which
        performs its own SSRF validation (connection to the resolved IP
        plus ``Host`` header), streaming size limits, and caching. This
        replaces the previous implementation that first fetched the
        image itself and then re-fetched the raw URL through
        ``_media_proxy.fetch`` — a double fetch that also handed the
        un-validated original URL to the proxy (a DNS-rebinding / SSRF
        hand-off).

        Returns:
            Proxied content ID or None if failed
        """
        if not self._media_proxy:
            return None

        try:
            proxied = self._media_proxy.fetch(image_url, force_refresh=False)
            if not proxied or not getattr(proxied, "id", None):
                return None

            # Block SVG content (can contain scripts). The media proxy also
            # rejects SVG at fetch time (both by Content-Type and by magic
            # bytes) so this is defense-in-depth; if an SVG slips through we
            # skip the image rather than fail the whole preview.
            content_type = (getattr(proxied, "content_type", "") or "").lower()
            if content_type.startswith("image/svg") or "svg+xml" in content_type:
                logger.warning(
                    f"Blocked image type for {image_url}: {content_type}; skipping image"
                )
                return None

            return proxied.id
        except ValueError as e:
            logger.warning(f"Blocked image for {image_url}: {e}; skipping image")
            return None
        except Exception as e:
            logger.warning(f"Failed to proxy image {image_url}: {e}")
            return None

    def generate_preview(
        self, user_id: int, url: str, skip_cache: bool = False
    ) -> PreviewMetadata:
        """
        Generate secure link preview.

        Args:
            user_id: User requesting preview
            url: URL to preview
            skip_cache: Force fresh fetch

        Returns:
            PreviewMetadata with extracted info

        Raises:
            ValueError: If URL is invalid or blocked
            RuntimeError: If rate limited
        """
        if not self._config["enabled"]:
            raise RuntimeError("Link previews are disabled")

        # Atomically consume the rate-limit budget BEFORE any further work so
        # concurrent requests cannot race past the limit. This applies to
        # cache hits as well so the limit cannot be bypassed by hammering a
        # cached URL.
        if not self._consume_rate_limit(user_id):
            raise RuntimeError("Rate limit exceeded for link previews")

        # Validate the URL (SSRF + scheme checks) BEFORE consulting ANY cache
        # so a cached entry can never be served for an unvalidated/unsafe URL.
        # This is a per-request gate: a cache hit never satisfies it, and it is
        # never skipped just because the same URL was resolved previously.
        hostname, resolved_ip, validated_url = self._validate_url(url)
        key = self._normalize_url(url)

        # Short-TTL, bounded in-process tier in front of the expensive remote
        # metadata resolution. The gate above already cleared THIS url on THIS
        # request, so serving from here cannot bypass SSRF protection.
        if not skip_cache:
            resolved = self._resolution_cache.get(key)
            if resolved is not None:
                logger.debug(f"Resolution cache hit for URL preview: {url}")
                # get() hands back a detached copy: a later request from a
                # different user cannot observe mutations made to this one.
                return resolved.metadata

        # Persistent cache (shared across processes) is the next tier.
        if not skip_cache:
            cached = self._get_cached_preview(url)
            if cached:
                logger.debug(f"Cache hit for URL preview: {url}")
                # Warm the short-TTL tier so a burst need not hit the DB again.
                self._resolution_cache.put(
                    key, cached.metadata, cached.proxied_image_id
                )
                return cached.metadata

        # Both caches missed. Coalesce concurrent identical resolutions so a
        # burst for the same cold URL pays for one remote fetch/parse/proxy.
        # Every caller above has already consumed its own rate-limit budget and
        # cleared the SSRF gate for this exact URL, so sharing the pure-derived
        # preview metadata cannot bypass either check. Waiters get a detached
        # copy, and the registry is capped so hostile distinct-URL floods cannot
        # pin unbounded waiter state.
        flight: Optional[_PreviewFlight] = None
        leader = False
        with self._flights_lock:
            # A forced refresh must never ride a flight started before it.
            existing = None if skip_cache else self._flights.get(key)
            if existing is not None:
                flight = existing
                self._flight_stats["joined"] += 1
            elif len(self._flights) >= self._max_inflight_resolutions:
                # At the bound: resolve independently instead of registering.
                self._flight_stats["rejected"] += 1
                leader = True
            else:
                flight = _PreviewFlight(threading.Event())
                self._flights[key] = flight
                leader = True
                self._flight_stats["led"] += 1
                self._flight_stats["peak"] = max(
                    self._flight_stats["peak"], len(self._flights)
                )

        if not leader:
            assert flight is not None
            # The leader cleared the gate with the same validated URL. Only
            # successful results are published; a failure is re-raised to every
            # waiter but never cached, so a later request can retry cleanly.
            flight.event.wait()
            if flight.error is not None:
                raise flight.error
            assert flight.metadata is not None
            return _clone_preview_metadata(flight.metadata)

        try:
            metadata = self._resolve_preview(
                url, key, validated_url, hostname, resolved_ip
            )
        except BaseException as exc:
            if flight is not None:
                with self._flights_lock:
                    flight.error = exc
                    if self._flights.get(key) is flight:
                        self._flights.pop(key, None)
                flight.event.set()
            raise

        if flight is not None:
            with self._flights_lock:
                # Store a detached copy so a later mutation of the leader's
                # returned object cannot race a waiter's clone.
                flight.metadata = _clone_preview_metadata(metadata)
                if self._flights.get(key) is flight:
                    self._flights.pop(key, None)
            flight.event.set()
        return metadata

    def _resolve_preview(
        self,
        url: str,
        key: str,
        validated_url: str,
        hostname: str,
        resolved_ip: str,
    ) -> PreviewMetadata:
        """Perform the expensive, cache-miss resolution (leader path only)."""
        # Fetch with secure redirect handling
        html, final_url, content_type = self._fetch_with_redirects(
            validated_url, hostname, resolved_ip
        )

        # Parse metadata
        metadata = self._parse_metadata(html, final_url)

        # Proxy image if enabled. The media proxy validates the image (SSRF,
        # content-type and magic-byte checks) and serves it through the
        # authenticated /media/proxy route. On success the stored/returned
        # image URL is rewritten to that route so clients only ever receive
        # content-validated bytes, never the raw external URL (which would
        # otherwise bypass the proxy's checks). The previously-dead
        # ``_validate_image_url`` defence-in-depth check is exercised here.
        proxied_image_id = None
        if self._config["proxy_images"] and metadata.image_url:
            try:
                self._validate_image_url(metadata.image_url)
            except ValueError as e:
                logger.warning(f"Dropping preview image {metadata.image_url}: {e}")
                metadata.image_url = None
            else:
                proxied_image_id = self._proxy_image(metadata.image_url)
                if proxied_image_id is not None:
                    api_prefix = self._config.get("api_prefix", "/api/v1")
                    metadata.image_url = (
                        f"{api_prefix}/media/proxy?url="
                        f"{quote(metadata.image_url, safe='')}"
                    )
                else:
                    # Proxy refused to serve this image (e.g. SVG or fetch
                    # failure); do not expose the raw external URL to clients.
                    metadata.image_url = None

        # Publish to both tiers. skip_cache only bypasses reads; an explicit
        # refresh must still replace any previously cached resolution.
        self._cache_preview(url, metadata, proxied_image_id)
        self._resolution_cache.put(key, metadata, proxied_image_id)

        logger.debug(f"Generated preview for: {url}")
        return metadata

    def resolution_inflight_stats(self) -> Dict[str, Any]:
        """Expose in-flight coalescing counters for observability and tests."""
        with self._flights_lock:
            result = dict(self._flight_stats)
            result["inflight"] = len(self._flights)
            return result

    def resolution_cache_stats(self) -> Dict[str, Any]:
        """Expose resolution-cache counters for observability and tests."""
        return self._resolution_cache.stats()

    def clear_resolution_cache(self) -> None:
        """Drop all in-process resolution cache entries."""
        self._resolution_cache.clear()

    def cleanup_expired_cache(self) -> int:
        """
        Remove expired cache entries.

        Returns:
            Number of entries removed
        """
        now = int(time.time() * 1000)
        result = self._db.execute(
            "DELETE FROM embed_preview_cache WHERE expires_at < ?", (now,)
        )
        return result.rowcount if hasattr(result, "rowcount") else 0
