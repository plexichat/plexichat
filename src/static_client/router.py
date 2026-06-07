"""
Static client HTTP serving.

Implements a Starlette-compatible ASGI middleware that:

* Serves files from the on-disk install directory with ETag / 304 / Range
  support.
* Applies per-content-type Cache-Control headers (hashed assets, HTML, other).
* Adds the configured security headers (CSP, X-Frame-Options, ...).
* Performs SPA-style fallback: configurable prefix -> html file, plus
  fallback to ``index.html`` for unknown paths.
* Acts as the outermost middleware so static paths never reach the auth
  or rate-limit middlewares.

The set of API paths that *must* be excluded from SPA fallback is derived
from the runtime config: ``api.api_prefix``, ``admin_ui.path`` and
``docs.path``. Anything outside those prefixes that isn't a static asset is
treated as a client route and served ``index.html``.
"""

from __future__ import annotations

import hmac
import mimetypes
import re
from dataclasses import dataclass
from email.utils import formatdate
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import unquote

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

import utils.logger as logger

_ROUTE_HTML = "static_client:html"
_ROUTE_ASSETS = "static_client:assets"


_HTML_EXT = (".html", ".htm")
_HASHED_EXTS = (".js", ".css", ".mjs", ".map", ".woff", ".woff2", ".ttf", ".otf")
_HASHED_FILE_HINTS = (
    "index-",
    "app-",
    "settings-",
    "register-",
    "forgot-password-",
    "reset-password-",
    "oauth-callback-",
    "error-",
    "entry-",
    "state-",
    "crypto-",
    "api-",
    "channel-",
    "messages-",
)

# Paths the static client treats as its own (prefix match)
_STATIC_PREFIXES = (
    "/assets/",
    "/css/",
    "/js/",
    "/favicon",
    "/apple-touch-icon",
    "/config.js",
    "/robots.txt",
    "/manifest",
    "/web-app-manifest",
    "/.well-known/",
)

# Paths the static client must never claim (exact + prefix match)
_API_EXACT_EXCLUDES = ("/",)
_API_PREFIX_EXCLUDES_DEFAULT = (
    "/api/",
    "/admin",
    "/admin/",
    "/docs",
    "/docs/",
    "/openapi.json",
    "/redoc",
    "/gateway",
    "/health",
    "/status",
    "/static",
)


@dataclass(frozen=True)
class StaticPaths:
    """API paths excluded from SPA fallback and the static prefix list."""

    api_prefix: str
    admin_path: str
    docs_path: str
    static_prefixes: Tuple[str, ...]
    api_prefixes: Tuple[str, ...]
    static_files: Tuple[str, ...]


def _build_paths(
    spa_routes: dict, api_prefix: str, admin_path: str, docs_path: str
) -> StaticPaths:
    """Derive the path set used by the static client."""
    api_prefix = (api_prefix or "/api/v1").rstrip("/") or "/api/v1"
    admin_path = admin_path or "/admin"
    if not admin_path.startswith("/"):
        admin_path = f"/{admin_path}"
    admin_path = admin_path.rstrip("/") or "/admin"
    docs_path = docs_path or "/docs/api"
    if not docs_path.startswith("/"):
        docs_path = f"/{docs_path}"
    docs_path = docs_path.rstrip("/") or "/docs/api"

    api_prefixes = _API_PREFIX_EXCLUDES_DEFAULT + (
        api_prefix,
        api_prefix + "/",
        admin_path,
        admin_path + "/",
        docs_path,
        docs_path + "/",
    )

    static_prefixes = _STATIC_PREFIXES + tuple(
        sorted(spa_routes.keys(), key=len, reverse=True)
    )
    return StaticPaths(
        api_prefix=api_prefix,
        admin_path=admin_path,
        docs_path=docs_path,
        static_prefixes=static_prefixes,
        api_prefixes=tuple(dict.fromkeys(api_prefixes)),
        static_files=("/favicon.svg", "/config.js", "/robots.txt"),
    )


def get_static_client_paths() -> Optional[StaticPaths]:
    """Return the path set used by the static client, or ``None`` if disabled."""
    from . import get_static_client_manager

    mgr = get_static_client_manager()
    if mgr is None or not mgr.config.serve:
        return None
    api_prefix = "/api/v1"
    admin_path = "/admin"
    docs_path = "/docs/api"
    try:
        import utils.config as _cfg

        api_prefix = _cfg.get("api", {}).get("api_prefix", "/api/v1") or "/api/v1"
        admin_path = _cfg.get("admin_ui", {}).get("path", "/admin") or "/admin"
        docs_path = _cfg.get("docs", {}).get("path", "/docs/api") or "/docs/api"
    except RuntimeError:
        pass
    return _build_paths(mgr.config.spa_routes, api_prefix, admin_path, docs_path)


@dataclass(frozen=True)
class _ResolvedFile:
    file_path: Path
    cache_control: str
    is_html: bool


def _scope_client_ip(scope: Scope) -> Optional[str]:
    """Best-effort extraction of the client IP from an ASGI scope."""
    client = scope.get("client")
    if isinstance(client, (tuple, list)) and client:
        host = client[0]
        if host:
            return str(host)
    for name, value in scope.get("headers") or []:
        if isinstance(name, (bytes, bytearray)) and name.lower() == b"x-forwarded-for":
            try:
                raw = (
                    value.decode("latin-1") if isinstance(value, bytes) else str(value)
                )
            except Exception:
                continue
            ip = raw.split(",")[0].strip()
            if ip:
                return ip
    return None


def _scope_is_localhost(scope: Scope) -> bool:
    client = scope.get("client")
    if isinstance(client, (tuple, list)) and client:
        host = client[0]
        return host in ("127.0.0.1", "::1", "localhost")
    return False


def _scope_header(scope: Scope, name: bytes) -> Optional[str]:
    for k, v in scope.get("headers") or []:
        if isinstance(k, (bytes, bytearray)) and k.lower() == name:
            if isinstance(v, bytes):
                return v.decode("latin-1", errors="replace")
            return str(v)
    return None


def _is_internal_scope(scope: Scope) -> bool:
    """Return True if the ASGI scope represents a trusted self-test request."""
    if not _scope_is_localhost(scope):
        return False
    try:
        import src.api as _api

        secret = _api.get_internal_secret()
    except Exception:
        secret = None
    if not secret:
        return False
    provided = _scope_header(scope, b"x-plexichat-internal-secret")
    if not provided:
        return False
    try:
        return hmac.compare_digest(provided, secret)
    except Exception:
        return False


@dataclass(frozen=True)
class _RateLimitOutcome:
    allowed: bool
    retry_after: Optional[int]
    headers: Tuple[Tuple[bytes, bytes], ...]


def _check_static_client_rate_limit(
    scope: Scope, route_key: str, enabled: bool = True
) -> _RateLimitOutcome:
    """Run a rate limit check via the shared rate limiter.

    Returns an ``_RateLimitOutcome`` describing whether the request is
    allowed, the suggested ``Retry-After`` (when blocked) and any
    informational headers to surface to the client.
    """
    if not enabled:
        return _RateLimitOutcome(True, None, ())

    try:
        from src.core import ratelimit

        if not ratelimit.is_setup():
            return _RateLimitOutcome(True, None, ())
    except Exception:
        return _RateLimitOutcome(True, None, ())

    ip = _scope_client_ip(scope)
    if not ip:
        return _RateLimitOutcome(True, None, ())

    is_internal = _is_internal_scope(scope)
    if is_internal:
        return _RateLimitOutcome(True, None, ())

    try:
        result = ratelimit.check_rate_limit(
            ip_address=ip,
            route=route_key,
            is_internal=False,
        )
    except Exception as exc:
        logger.warning(f"static_client: rate limit check failed: {exc}")
        return _RateLimitOutcome(True, None, ())

    info_headers: List[Tuple[bytes, bytes]] = []
    try:
        raw_headers = result.headers.to_dict()
    except Exception:
        raw_headers = {}
    for k, v in raw_headers.items():
        if k.lower() in ("retry-after",):
            continue
        try:
            info_headers.append((k.encode(), str(v).encode()))
        except Exception:
            continue

    if not result.allowed:
        retry_after: Optional[int] = None
        try:
            if result.retry_after is not None:
                retry_after = max(1, int(result.retry_after) + 1)
        except Exception:
            retry_after = None
        if retry_after is None:
            try:
                reset = float(getattr(result.headers, "reset_after", 0) or 0)
                if reset > 0:
                    retry_after = max(1, int(reset))
            except Exception:
                retry_after = None
        retry_header: Tuple[Tuple[bytes, bytes], ...] = ()
        if retry_after is not None:
            retry_header = ((b"retry-after", str(retry_after).encode()),)
        return _RateLimitOutcome(False, retry_after, retry_header)

    return _RateLimitOutcome(True, None, tuple(info_headers))


class StaticClientMiddleware:
    """ASGI middleware that serves the Plexichat web client."""

    def __init__(
        self,
        app: ASGIApp,
        manager,
        config,
    ):
        self._app = app
        self._mgr = manager
        self._cfg = config
        self._paths = _build_paths(
            config.spa_routes,
            self._detect_api_prefix(),
            self._detect_admin_path(),
            self._detect_docs_path(),
        )
        self._spa_routes = config.sorted_spa_routes()
        self._spa_matchers = [
            (self._compile_prefix(prefix), html_file)
            for prefix, html_file in self._spa_routes
        ]
        self._invite_redirect_re = re.compile(r"^/invite/([^/]+)$")
        self._rate_limit_enabled = bool(getattr(config.rate_limit, "enabled", True))

    @staticmethod
    def _compile_prefix(prefix: str) -> re.Pattern[str]:
        pfx = prefix.rstrip("/") or "/"
        if pfx == "/":
            return re.compile(r"^/$")
        return re.compile(rf"^/{re.escape(pfx.lstrip('/'))}(/.*)?$")

    def _detect_api_prefix(self) -> str:
        try:
            import utils.config as _cfg

            return _cfg.get("api", {}).get("api_prefix", "/api/v1") or "/api/v1"
        except RuntimeError:
            return "/api/v1"

    def _detect_admin_path(self) -> str:
        try:
            import utils.config as _cfg

            return _cfg.get("admin_ui", {}).get("path", "/admin") or "/admin"
        except RuntimeError:
            return "/admin"

    def _detect_docs_path(self) -> str:
        try:
            import utils.config as _cfg

            return _cfg.get("docs", {}).get("path", "/docs/api") or "/docs/api"
        except RuntimeError:
            return "/docs/api"

    def _is_html_request(self, path: str) -> bool:
        """Return True if the request path targets an HTML response."""
        if not path:
            return True
        ext = Path(path).suffix.lower()
        if ext in _HTML_EXT:
            return True
        if ext in _HASHED_EXTS:
            return False
        name = Path(path).name
        if name == "config.js" or name == "robots.txt" or name == "favicon.svg":
            return False
        if path.startswith(("/assets/", "/js/", "/css/")):
            return False
        for matcher, _ in self._spa_matchers:
            if matcher.match(path):
                return True
        if path == "/" or path == "/index.html":
            return True
        return False

    def is_handled(self, path: str) -> bool:
        """Return True if *path* should be served by this middleware."""
        if not path:
            return False
        # Exact-match static files are always ours
        if path in self._paths.static_files:
            return True
        # SPA prefixes / static asset prefixes are always ours
        for prefix in self._paths.static_prefixes:
            if path == prefix or path.startswith(prefix):
                return True
        # Any path that looks like a hashed asset (under /assets/, /js/, /css/)
        if path.startswith(("/assets/", "/js/", "/css/")):
            return True
        # Hashed file extensions in any other location are also static
        ext = Path(path).suffix.lower()
        if ext in _HASHED_EXTS:
            return True
        # HTML files: ours (the API doesn't serve .html)
        if ext in _HTML_EXT:
            return True
        # "/" and "/index.html" are ours
        if path == "/" or path == "/index.html":
            return True
        # Exclude API prefixes
        for prefix in self._paths.api_prefixes:
            if path == prefix or path.startswith(prefix):
                return False
        # Anything else: SPA fallback (we will serve index.html)
        return True

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        if method not in ("GET", "HEAD"):
            await self._app(scope, receive, send)
            return

        path = scope.get("path", "") or "/"
        if not self.is_handled(path):
            await self._app(scope, receive, send)
            return

        # Strip Authorization so downstream (if anything leaks) never sees it
        headers = [
            (k, v)
            for k, v in scope.get("headers", [])
            if not (isinstance(k, (bytes, bytearray)) and k.lower() == b"authorization")
        ]
        scope = {**scope, "headers": headers}

        decoded = unquote(path) or "/"

        # Invite redirect: /invite/CODE -> 302 to /app.html?invite=CODE
        if self._cfg.invite_redirect:
            m = self._invite_redirect_re.match(decoded)
            if m:
                target = f"/app.html?invite={m.group(1)}"
                await self._send_redirect(send, target)
                return

        route_key = _ROUTE_HTML if self._is_html_request(decoded) else _ROUTE_ASSETS
        outcome = _check_static_client_rate_limit(
            scope, route_key, self._rate_limit_enabled
        )
        if not outcome.allowed:
            await self._send_rate_limited(send, outcome)
            return

        install_path = self._mgr.current_install_path()
        if install_path is None or not install_path.is_dir():
            await self._send_placeholder(send, outcome)
            return

        # /config.js is generated per-request so serverUrl reflects the
        # request's actual origin (Host / Origin header) rather than the
        # value baked into the on-disk file at install time.
        if Path(decoded).name == self._cfg.config_injection.filename:
            await self._send_runtime_config(send, scope, outcome)
            return

        resolved = self._resolve(install_path, decoded)
        if resolved is None:
            await self._send_not_found(send)
            return

        await self._send_file(send, resolved, method == "HEAD", scope, outcome)

    def _resolve(self, root: Path, path: str) -> Optional[_ResolvedFile]:
        rel = path.lstrip("/")
        if rel == "":
            candidate = root / "index.html"
        else:
            candidate = (root / rel).resolve()

        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            return None

        if candidate.is_file():
            return self._classify(candidate)

        if candidate.is_dir():
            index = candidate / "index.html"
            if index.is_file():
                return self._classify(index)
            return None

        for matcher, html_file in self._spa_matchers:
            if matcher.match(path):
                html_path = (root / html_file).resolve()
                try:
                    html_path.relative_to(root.resolve())
                except ValueError:
                    continue
                if html_path.is_file():
                    return _ResolvedFile(
                        file_path=html_path,
                        cache_control=self._cfg.cache_control.html,
                        is_html=True,
                    )

        return None

    def _classify(self, file_path: Path) -> _ResolvedFile:
        ext = file_path.suffix.lower()
        name = file_path.name
        path_str = str(file_path)

        if ext in _HTML_EXT or name == "config.js":
            return _ResolvedFile(
                file_path=file_path,
                cache_control=self._cfg.cache_control.html,
                is_html=True,
            )
        if (
            ext in _HASHED_EXTS
            or any(name.startswith(h) for h in _HASHED_FILE_HINTS)
            or "/assets/" in path_str
        ):
            return _ResolvedFile(
                file_path=file_path,
                cache_control=self._cfg.cache_control.hashed_assets,
                is_html=False,
            )
        return _ResolvedFile(
            file_path=file_path,
            cache_control=self._cfg.cache_control.other,
            is_html=False,
        )

    def _build_headers(
        self,
        resolved: _ResolvedFile,
        content_length: int,
        scope: Scope,
        rate_limit_headers: Tuple[Tuple[bytes, bytes], ...] = (),
    ) -> List[Tuple[bytes, bytes]]:
        sec = self._cfg.security_headers
        headers: List[Tuple[bytes, bytes]] = [
            (b"content-type", self._guess_content_type(resolved.file_path).encode()),
            (b"content-length", str(content_length).encode()),
            (b"cache-control", resolved.cache_control.encode()),
            (b"x-content-type-options", sec.x_content_type_options.encode()),
            (b"x-frame-options", sec.x_frame_options.encode()),
            (b"referrer-policy", sec.referrer_policy.encode()),
            (b"permissions-policy", sec.permissions_policy.encode()),
            (b"content-security-policy", sec.content_security_policy.encode()),
        ]
        origin = self._extract_origin_header(scope)
        if origin:
            headers.append((b"access-control-allow-origin", origin.encode()))
        headers.append((b"vary", b"Accept-Encoding, Origin"))
        for k, v in rate_limit_headers:
            headers.append((k, v))
        return headers

    @staticmethod
    def _guess_content_type(path: Path) -> str:
        ctype, _ = mimetypes.guess_type(str(path))
        if ctype is None:
            ext = path.suffix.lower()
            if ext == ".js":
                return "application/javascript"
            if ext == ".mjs":
                return "application/javascript"
            if ext == ".map":
                return "application/json"
            if ext == ".svg":
                return "image/svg+xml"
            if ext == ".woff":
                return "font/woff"
            if ext == ".woff2":
                return "font/woff2"
            return "application/octet-stream"
        if ctype == "text/javascript":
            return "application/javascript"
        return ctype

    @staticmethod
    def _extract_origin_header(scope: Scope) -> Optional[str]:
        for k, v in scope.get("headers", []):
            if isinstance(k, (bytes, bytearray)) and k.lower() == b"origin":
                if isinstance(v, bytes):
                    return v.decode("latin-1", errors="replace")
                return str(v)
        return None

    @staticmethod
    def _request_origin(scope: Scope) -> str:
        """Return a public origin for this request, e.g. ``http://chat.example.com``.

        Uses the ``Origin`` header if present, then falls back to the
        ``Host`` header combined with the scheme. Returns ``""`` if no
        host info is available.
        """
        # 1) Origin header (most reliable for cross-origin requests)
        origin = StaticClientMiddleware._extract_origin_header(scope)
        if origin:
            return origin
        # 2) Host header (typical for same-origin browser requests)
        for k, v in scope.get("headers", []):
            if isinstance(k, (bytes, bytearray)) and k.lower() == b"host":
                if isinstance(v, bytes):
                    host_value = v.decode("latin-1", errors="replace")
                else:
                    host_value = str(v)
                if host_value:
                    scheme = scope.get("scheme", "http")
                    return f"{scheme}://{host_value}"
        return ""

    async def _send_runtime_config(
        self,
        send: Send,
        scope: Scope,
        rate_limit_outcome: Optional["_RateLimitOutcome"] = None,
    ) -> None:
        """Send /config.js with the request's actual origin.

        Generated dynamically so ``serverUrl`` reflects the Host/Origin
        the user actually came in from, not a value baked into the
        on-disk install at config-time.
        """
        try:
            version = self._mgr.current_version() or ""
        except Exception:
            version = ""
        if not version:
            await self._send_not_found(send)
            return
        from .manager import _render_config_js

        request_origin = self._request_origin(scope)
        # Honour the explicit public_server_url override; only fall back
        # to the request origin if it is non-empty.
        configured = (self._cfg.config_injection.public_server_url or "").strip()
        if configured:
            origin = configured
        elif request_origin:
            origin = request_origin
        else:
            origin = self._mgr._detect_origin()
        body = _render_config_js(
            self._cfg.config_injection.content, origin, version
        ).encode("utf-8")
        rl_headers = (
            rate_limit_outcome.headers if rate_limit_outcome is not None else ()
        )
        sec = self._cfg.security_headers
        headers: List[Tuple[bytes, bytes]] = [
            (b"content-type", b"application/javascript; charset=utf-8"),
            (b"content-length", str(len(body)).encode()),
            (
                b"cache-control",
                self._cfg.cache_control.html.encode(),
            ),
            (b"x-content-type-options", sec.x_content_type_options.encode()),
            (b"x-frame-options", sec.x_frame_options.encode()),
            (b"referrer-policy", sec.referrer_policy.encode()),
            (b"permissions-policy", sec.permissions_policy.encode()),
            (b"content-security-policy", sec.content_security_policy.encode()),
        ]
        for hk, hv in rl_headers:
            headers.append((hk, hv))
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def _send_file(
        self,
        send: Send,
        resolved: _ResolvedFile,
        head_only: bool,
        scope: Scope,
        rate_limit_outcome: Optional[_RateLimitOutcome] = None,
    ) -> None:
        try:
            stat = resolved.file_path.stat()
        except OSError:
            await self._send_not_found(send)
            return

        etag = f'"{(resolved.file_path.stat().st_mtime_ns):x}-{(stat.st_size):x}"'
        etag_bytes = etag.encode()

        if_none_match = self._header(scope, b"if-none-match")
        if if_none_match and self._etag_matches(if_none_match, etag):
            await send(
                {
                    "type": "http.response.start",
                    "status": 304,
                    "headers": [
                        (b"etag", etag_bytes),
                        (b"cache-control", resolved.cache_control.encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return

        if_range = self._header(scope, b"if-range")
        range_header = self._header(scope, b"range")
        size = stat.st_size
        start = 0
        end = size - 1
        is_partial = False

        if (
            range_header
            and range_header.startswith("bytes=")
            and (not if_range or if_range == etag)
        ):
            try:
                range_val = range_header[len("bytes=") :]
                if "-" in range_val:
                    s, e = range_val.split("-", 1)
                    if s:
                        start = int(s)
                    if e:
                        end = int(e)
                    end = min(end, size - 1)
                    if start <= end and 0 <= start < size:
                        is_partial = True
                    else:
                        start = 0
                        end = size - 1
            except ValueError:
                start = 0
                end = size - 1

        content_length = end - start + 1
        rl_headers = (
            rate_limit_outcome.headers if rate_limit_outcome is not None else ()
        )
        headers = self._build_headers(resolved, content_length, scope, rl_headers)
        headers.append((b"etag", etag_bytes))
        last_modified = formatdate(timeval=stat.st_mtime, usegmt=True).encode()
        headers.append((b"last-modified", last_modified))
        headers.append((b"accept-ranges", b"bytes"))

        if is_partial:
            headers.append((b"content-range", f"bytes {start}-{end}/{size}".encode()))

        status = 206 if is_partial else 200
        await send(
            {"type": "http.response.start", "status": status, "headers": headers}
        )

        if head_only:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return

        try:
            with open(resolved.file_path, "rb") as fp:
                if start > 0:
                    fp.seek(start)
                remaining = content_length
                while remaining > 0:
                    chunk = fp.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    await send(
                        {
                            "type": "http.response.body",
                            "body": chunk,
                            "more_body": True,
                        }
                    )
        except OSError as exc:
            logger.warning(f"static_client: read error {resolved.file_path}: {exc}")
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    @staticmethod
    def _header(scope: Scope, name: bytes) -> Optional[str]:
        for k, v in scope.get("headers", []):
            if isinstance(k, (bytes, bytearray)) and k.lower() == name:
                if isinstance(v, bytes):
                    return v.decode("latin-1", errors="replace")
                return str(v)
        return None

    @staticmethod
    def _etag_matches(header_value: str, etag: str) -> bool:
        candidates = [c.strip() for c in header_value.split(",")]
        for c in candidates:
            if c == etag or c == "*":
                return True
        return False

    async def _send_not_found(self, send: Send) -> None:
        body = b"Not Found"
        await send(
            {
                "type": "http.response.start",
                "status": 404,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                    (b"x-content-type-options", b"nosniff"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def _send_rate_limited(self, send: Send, outcome: _RateLimitOutcome) -> None:
        body = b'{"error":"rate_limited","message":"Too many requests."}'
        headers: List[Tuple[bytes, bytes]] = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode()),
            (b"x-content-type-options", b"nosniff"),
            (b"cache-control", b"no-store"),
        ]
        for k, v in outcome.headers:
            headers.append((k, v))
        if outcome.retry_after is not None and not any(
            k.lower() == b"retry-after" for k, _ in outcome.headers
        ):
            headers.append((b"retry-after", str(outcome.retry_after).encode()))
        await send({"type": "http.response.start", "status": 429, "headers": headers})
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def _send_placeholder(
        self,
        send: Send,
        rate_limit_outcome: Optional[_RateLimitOutcome] = None,
    ) -> None:
        body = (
            b"<!DOCTYPE html><html><head><title>Plexichat</title>"
            b"<meta charset='utf-8'>"
            b"<style>body{font-family:system-ui;background:#0b0f19;color:#f9fafb;"
            b"display:flex;align-items:center;justify-content:center;height:100vh;"
            b"margin:0;}</style></head><body><div><h1>Plexichat</h1>"
            b"<p>The web client is not installed yet. "
            b"Set <code>static_client.enabled: true</code> in config.yaml "
            b"or place a dist under <code>~/.plexichat/client/&lt;version&gt;</code> "
            b"and create <code>current_version</code> pointing at it.</p></div></body></html>"
        )
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"text/html; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                    (b"retry-after", b"30"),
                    (b"x-content-type-options", b"nosniff"),
                    *(
                        rate_limit_outcome.headers
                        if rate_limit_outcome is not None
                        else ()
                    ),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def _send_redirect(self, send: Send, location: str) -> None:
        body = b""
        await send(
            {
                "type": "http.response.start",
                "status": 302,
                "headers": [
                    (b"location", location.encode()),
                    (b"content-length", b"0"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})


def install_static_client_middleware(app: FastAPI) -> Optional[StaticClientMiddleware]:
    """Install the static-client middleware at the outermost position.

    Returns the installed middleware, or ``None`` if the feature is disabled.
    """
    from . import get_static_client_manager

    mgr = get_static_client_manager()
    if mgr is None or not mgr.config.serve:
        return None

    app.add_middleware(StaticClientMiddleware, manager=mgr, config=mgr.config)
    return None


__all__ = [
    "StaticClientMiddleware",
    "StaticPaths",
    "get_static_client_paths",
    "install_static_client_middleware",
]
