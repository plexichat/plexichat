"""
Security headers middleware - Adds standard security headers to all responses.
"""

from typing import Sequence

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Send, Scope

from .response_policy import enforce_response_policy


def _resolve_api_prefix() -> str:
    """Resolve the configurable API prefix, falling back to the default.

    The CSP scoping below must follow ``api.api_prefix``; hardcoding
    ``/api/v1`` silently downgraded the strict API policy to the permissive
    SPA policy on any deployment that renamed the prefix.
    """
    try:
        from src.api.config import get_api_config

        return get_api_config().api_prefix.rstrip("/") or "/api/v1"
    except Exception:
        return "/api/v1"


def _cors_settings() -> tuple[Sequence[str], bool]:
    """Read the CORS allowlist, failing closed when config is unavailable."""
    try:
        from src.api.config import get_api_config

        settings = get_api_config()
        return tuple(settings.cors_origins), bool(settings.cors_allow_credentials)
    except Exception:
        return (), True


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
        api_prefix = _resolve_api_prefix()
        cors_origins, allow_credentials = _cors_settings()
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
