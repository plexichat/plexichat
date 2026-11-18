"""
Security headers middleware - Adds standard security headers to all responses.
"""

from typing import Sequence

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Send, Scope

from .response_policy import enforce_response_policy


def _security_policy() -> tuple[str, tuple[str, ...], bool]:
    """Resolve every config-derived input to the response policy in ONE read.

    ``__call__`` used to call :func:`_resolve_api_prefix` and
    :func:`_cors_settings` separately, and each of those called
    ``get_api_config()``. That helper serves a :class:`VersionedConfigSnapshot`
    whose version token stats the config file on every access, so a single HTTP
    response paid for two identical config reads (two ``stat`` syscalls plus two
    snapshot lock acquisitions) before any header was stamped.

    The values themselves are request-invariant, so read the snapshot once and
    derive all three inputs from it. Each field keeps its own fail-closed
    default, identical to the two helpers it replaces, so a partial/malformed
    config still downgrades only that field and never weakens the policy:

    * missing/invalid ``api_prefix`` -> ``/api/v1`` (strict API CSP scoped to
      the default prefix, matching the old fallback);
    * missing/invalid CORS settings -> empty allowlist with credentials "on"
      (``_enforce_cors`` then strips every reflected origin, the old fallback).

    Reading once also makes the three values atomic with respect to a config
    reload: a reload between the old two calls could pair one version's prefix
    with another version's allowlist.
    """
    prefix = "/api/v1"
    origins: tuple[str, ...] = ()
    allow_credentials = True
    try:
        from src.api.config import get_api_config

        settings = get_api_config()
    except Exception:
        return prefix, origins, allow_credentials

    try:
        prefix = settings.api_prefix.rstrip("/") or "/api/v1"
    except Exception:
        pass
    try:
        origins = tuple(settings.cors_origins)
        allow_credentials = bool(settings.cors_allow_credentials)
    except Exception:
        origins, allow_credentials = (), True
    return prefix, origins, allow_credentials


def _resolve_api_prefix() -> str:
    """Resolve the configurable API prefix, falling back to the default.

    Kept for callers outside this module (``IPBlockingMiddleware`` stamps its
    own 403 responses with the same policy). The CSP scoping must follow
    ``api.api_prefix``; hardcoding ``/api/v1`` silently downgraded the strict
    API policy to the permissive SPA policy on a renamed prefix.
    """
    return _security_policy()[0]


def _cors_settings() -> tuple[Sequence[str], bool]:
    """Read the CORS allowlist, failing closed when config is unavailable."""
    origins, allow_credentials = _security_policy()[1:]
    return origins, allow_credentials


def _log_response_failure(exc: Exception) -> None:
    """Report an unhandled downstream error; never break the error response."""
    try:
        import utils.logger as logger

        logger.error(
            "unhandled error in security-headers middleware: %s", exc, exc_info=exc
        )
    except Exception:
        pass


class SecurityHeadersMiddleware:
    """ASGI middleware to add security headers to all HTTP responses."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        api_prefix, cors_origins, allow_credentials = _security_policy()
        response_started = False

        async def send_wrapper(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                headers = list(message.get("headers", []))
                message = dict(message)
                message["headers"] = enforce_response_policy(
                    headers,
                    path=path,
                    api_prefix=api_prefix,
                    cors_origins=cors_origins,
                    cors_allow_credentials=allow_credentials,
                )
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            if response_started:
                # A partially-sent response cannot be replaced; re-raise so
                # the server handles the broken connection as usual.
                raise
            # Starlette's ServerErrorMiddleware lives OUTSIDE application
            # middleware. Without this catch, its 500 response would bypass
            # every security header. Emit the error here so even crash paths
            # are stamped with the canonical policy.
            _log_response_failure(exc)
            response = JSONResponse(
                status_code=500, content={"detail": "Internal Server Error"}
            )
            await response(scope, receive, send_wrapper)
