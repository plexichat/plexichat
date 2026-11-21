"""
Logging middleware - Request/response logging with accurate timing.

This middleware measures the COMPLETE request duration from when the request
is received until the response body is fully sent to the client.

Also collects server-side telemetry for comparison with client-reported latency.
"""

import time
from fastapi.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Receive, Send, Scope, Message

from .request_context import cached_client_ip


def _get_logger():
    """Get the logger module dynamically to ensure we use the configured instance."""
    try:
        import utils.logger as logger

        return logger
    except ImportError:
        return None


def _submit_server_telemetry(
    endpoint: str,
    method: str,
    response_time_ms: float,
    status_code: int,
    db_queries: int = 0,
    db_time_ms: float = 0.0,
):
    """Submit server-side telemetry data."""
    try:
        from src.core import telemetry

        if telemetry.is_setup():
            telemetry.submit_response_times(
                [
                    {
                        "endpoint": endpoint,
                        "method": method,
                        "response_time_ms": response_time_ms,
                        "status_code": status_code,
                        "timestamp": int(time.time() * 1000),
                        "db_queries": db_queries,
                        "db_time_ms": db_time_ms,
                    }
                ],
                client_id="server",
            )
    except Exception as exc:
        # Telemetry is fire-and-forget: it must never fail the request, but a
        # failure has to be visible somewhere instead of vanishing.
        _log_debug(f"server telemetry submit failed: {type(exc).__name__}: {exc}")


def _log_debug(msg: str):
    """Log debug message if logger is available and configured."""
    logger = _get_logger()
    if logger:
        try:
            logger.debug(msg)
        except RuntimeError:
            pass


def _log_info(msg: str):
    """Log info message if logger is available and configured."""
    logger = _get_logger()
    if logger:
        try:
            logger.info(msg)
        except RuntimeError as e:
            print(f"[LOGGING MIDDLEWARE] RuntimeError: {e}")
        except Exception as e:
            print(f"[LOGGING MIDDLEWARE] Exception: {e}")
    else:
        print(f"[LOGGING MIDDLEWARE] Logger not available: {msg}")


def _log_warning(msg: str):
    """Log warning message if logger is available and configured."""
    logger = _get_logger()
    if logger:
        try:
            logger.warning(msg)
        except RuntimeError:
            pass


def _log_error(msg: str):
    """Log error message if logger is available and configured."""
    logger = _get_logger()
    if logger:
        try:
            logger.error(msg)
        except RuntimeError:
            pass


# Skip logging for these paths to reduce noise
SKIP_PATHS = frozenset(["/api/v1/health", "/health", "/favicon.ico"])


def _safe_log_path(scope: Scope, raw_path: str) -> str:
    """Return a request path that is safe to persist in the access log.

    ``scope["path"]`` is the *resolved* path. Routes that carry a credential
    in a path segment -- most notably ``POST /webhooks/{webhook_id}/{token}``,
    whose ``token`` segment is a live webhook secret -- would otherwise write
    that secret to ``latest.log`` on every execution. Starlette records the
    matched route template (each parameter replaced by ``{name}``) in
    ``scope["route"]``; use it whenever routing metadata is available, exactly
    as the telemetry branch in ``send_wrapper`` already does. When routing
    metadata is absent, redact the credential-bearing segment of webhook
    execution paths rather than logging it verbatim.
    """
    route = scope.get("route")
    if route is not None:
        template = getattr(route, "path_format", None) or getattr(route, "path", None)
        if isinstance(template, str) and template:
            return template
    # No route metadata (e.g. a 404, or a path served by the SPA ASGI
    # middleware): still never emit a credential carried in a path segment.
    parts = raw_path.split("/")
    if len(parts) >= 3 and parts[-3] == "webhooks":
        return "/".join(parts[:-1]) + "/{token}"
    # ``/invite/{code}`` is redirected by StaticClientMiddleware (no FastAPI
    # route metadata), so its code would otherwise be written verbatim to the
    # access log on every join-link visit. The code is a bearer credential.
    if len(parts) >= 3 and parts[-2] == "invite":
        return "/".join(parts[:-1]) + "/{code}"
    return raw_path


class LoggingMiddleware:
    """
    ASGI middleware for logging HTTP requests and responses with accurate timing.

    This implementation uses raw ASGI to measure the complete request duration,
    including the time to fully send the response body to the client.
    The previous BaseHTTPMiddleware approach only measured until the response
    object was created, not until it was fully transmitted.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            # For now, skip detailed logging for WebSockets
            await self.app(scope, receive, send)
            return

        # Extract request info
        method = scope.get("method", "UNKNOWN")
        path = scope.get("path", "/")

        # Skip high-volume health/favicon probes before doing ANY per-request
        # work: they must not pay for trusted-proxy parsing or a DB reset.
        if path in SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        # Get client IP. M1: use the trusted-proxy-aware extractor so the IP
        # logged here matches what authentication/rate-limiting record (raw
        # ``scope["client"]`` logs the reverse-proxy address instead).
        try:
            from typing import Any, Dict, cast

            from src.utils.net import get_client_ip

            # IP blocking and authentication visit this scope too; reuse the
            # trusted-proxy result instead of parsing X-Forwarded-For again.
            client_ip = (
                cached_client_ip(cast(Dict[str, Any], scope), resolver=get_client_ip)
                or "unknown"
            )
        except Exception:
            client = scope.get("client")
            client_ip = client[0] if client else "unknown"

        # Reset DB metrics for the new request context
        try:
            import src.api as api

            db = api.get_db()
            if db:
                db.reset_request_metrics()
        except Exception as exc:
            # Per-request DB counters are diagnostics; without the reset the
            # request is still served, but the reason must be traceable.
            _log_debug(f"request metric reset failed: {type(exc).__name__}: {exc}")

        # Start timing from when request is received
        start_time = time.perf_counter()
        status_code = 0
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started

            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
                response_started = True
            elif message["type"] == "http.response.body":
                # Check if this is the final body chunk
                more_body = message.get("more_body", False)
                if not more_body:
                    # Response is complete - log the timing now
                    end_time = time.perf_counter()
                    duration_ms = (end_time - start_time) * 1000

                    # Generate and write log message in a threadpool to avoid HDD bottlenecks.
                    # Use a sanitized path, NOT the resolved path: webhook tokens and
                    # other credentials live in path segments and must never be
                    # written to the access log.
                    log_path = _safe_log_path(scope, path)
                    log_msg = f"{method} {log_path} - {status_code} - {duration_ms:.2f}ms - {client_ip}"

                    def write_log():
                        if status_code >= 500:
                            _log_error(log_msg)
                        elif status_code >= 400:
                            _log_warning(log_msg)
                        else:
                            _log_info(log_msg)

                    await run_in_threadpool(write_log)

                    # Submit server-side telemetry for API endpoints
                    if path.startswith("/api/"):
                        try:
                            # Get DB metrics if available
                            import src.api as api

                            db = api.get_db()
                            db_metrics = (
                                db.get_request_metrics()
                                if db
                                else {"query_count": 0, "query_time_ms": 0.0}
                            )

                            # Try to get normalized path (route template)
                            telemetry_path = path
                            if "route" in scope:
                                route = scope["route"]
                                # path_format is the standard attribute for the template (e.g. /api/v1/users/{user_id})
                                if hasattr(route, "path_format"):
                                    telemetry_path = route.path_format
                                elif hasattr(route, "path"):
                                    telemetry_path = route.path

                            await run_in_threadpool(
                                _submit_server_telemetry,
                                telemetry_path,
                                method,
                                duration_ms,
                                status_code,
                                db_metrics["query_count"],
                                db_metrics["query_time_ms"],
                            )
                        except Exception as exc:
                            _log_debug(
                                f"response telemetry failed: {type(exc).__name__}: {exc}"
                            )

            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as e:
            # Log error with timing. M1: log the exception CLASS only - the
            # full ``str(e)`` may embed sensitive request details, and the
            # real traceback is already written by the error handler.
            end_time = time.perf_counter()
            duration_ms = (end_time - start_time) * 1000
            _log_error(
                f"{method} {_safe_log_path(scope, path)} - ERROR - "
                f"{duration_ms:.2f}ms - {client_ip} - {type(e).__name__}"
            )
            raise
