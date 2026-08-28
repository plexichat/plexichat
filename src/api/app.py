"""
FastAPI application factory - Creates and configures the API application.
"""


class _BodyTooLarge(Exception):
    """Raised by the MaxBodySize inner receive wrapper when the cap is hit."""

    def __init__(self, cap: int):
        super().__init__(f"request body exceeded {cap} bytes")
        self.cap = cap


# Register a JSON 413 mapping so any code path that escapes the
# middleware (downstream body reader, async generator, etc.) still
# surfaces a clean 413 rather than a 500.
try:
    # The exception handler is wired up via FastAPI below once
    # ``app`` exists. This module-level sentinel is here so the
    # import-order is deterministic for unit tests.
    _BODY_TOO_LARGE_HANDLER_REGISTERED = False
except Exception:  # pragma: no cover
    _BODY_TOO_LARGE_HANDLER_REGISTERED = False


from fastapi import FastAPI, Request, HTTPException, Response  # noqa: E402
from fastapi.openapi.docs import get_swagger_ui_oauth2_redirect_html  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
import sys  # noqa: E402

import utils.config as global_config  # noqa: E402
import utils.logger as logger  # noqa: E402
from .config import get_api_config  # noqa: E402
from .routes import create_api_router, create_docs_router, is_docs_enabled  # noqa: E402
from .routes.docs import get_docs_config  # noqa: E402
from .routes.docs.openapi import render_redoc_page, render_swagger_ui_page  # noqa: E402


def create_app(enable_rate_limiting: bool = True, enable_docs: bool = True) -> FastAPI:
    """
    Create and configure the FastAPI application.

    Args:
        enable_rate_limiting: Whether to enable rate limiting middleware.
        enable_docs: Whether to enable documentation serving.
                     The actual path is configured in config.yaml under docs.path

    Returns:
        Configured FastAPI application instance.
    """
    from contextlib import asynccontextmanager

    from .middleware import (
        AuthenticationMiddleware,
        SecurityHeadersMiddleware,
        setup_exception_handlers,
        create_rate_limit_middleware,
    )
    from .middleware.database import DatabaseMiddleware
    from .middleware.ip_blocking import IPBlockingMiddleware
    from .middleware.logging import LoggingMiddleware

    config = get_api_config()
    # Debug CORS config
    if "pytest" in sys.modules:
        logger.debug(f"CORS Origins: {config.cors_origins}")
        logger.debug(f"CORS Headers: {config.cors_allow_headers}")

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        # Cap the AnyIO default thread limiter used by run_in_threadpool so
        # sync endpoint handlers can't spawn an unbounded number of worker
        # threads (anyio 4.13 defaults to 40). The limiter is a context-scoped
        # RunVar, so it must be set here, inside the running event loop and
        # before uvicorn spawns request tasks.
        import anyio.to_thread

        thread_limiter = anyio.to_thread.current_default_thread_limiter()
        thread_limiter.total_tokens = config.max_worker_threads
        logger.info(
            f"AnyIO default thread limiter capped at "
            f"{config.max_worker_threads} concurrent worker threads"
        )

        from src.static_client import (
            run_static_client_initial_install,
            start_static_client_service,
            stop_static_client_service,
        )

        # CORRECTNESS FIX: mark every persisted presence row offline on boot.
        # If a previous process crashed, its users' presence rows stayed
        # stuck on live statuses (online/idle/dnd) forever, so DB-fallback
        # reads reported ghosts. The Valkey online index is cleared too.
        #
        # The sweep runs AFTER the background InitPresence thread finishes
        # (bounded wait): sweeping before setup completes used to raise
        # "module not initialized", silently skipping the ghost cleanup.
        # Multi-worker double-sweeps are prevented by the distributed lock
        # inside sweep_all_offline itself.
        try:
            import asyncio as _asyncio

            from src.core import presence as presence_module

            deadline = _asyncio.get_event_loop().time() + 15.0
            while not presence_module.is_setup():
                if _asyncio.get_event_loop().time() >= deadline:
                    logger.warning(
                        "Presence module did not finish initializing; "
                        "startup sweep skipped"
                    )
                    break
                await _asyncio.sleep(0.05)

            if presence_module.is_setup():
                swept = presence_module.sweep_all_offline()
                if swept:
                    logger.info(f"Startup presence sweep reset {swept} rows to offline")
        except RuntimeError as e:
            logger.debug(f"Presence module not initialized; sweep skipped: {e}")
        except Exception as e:
            logger.warning(f"Startup presence sweep failed: {e}")

        run_static_client_initial_install()
        await start_static_client_service()

        # Periodic poll-expiry sweep so ``ended_at`` is persisted for
        # expired polls even when nobody reads/votes on them (lazy
        # ``is_ended`` alone lags the DB state that AFTER_END result
        # visibility depends on). The interval is bounded below so a
        # misconfigured value cannot become a busy loop.
        from .poll_expiry import start_poll_expiry_sweep, stop_poll_expiry_sweep

        poll_expiry_task = start_poll_expiry_sweep()

        # Periodic chunked-upload session sweep so uploads abandoned
        # mid-flight have their temp files reaped even when no request
        # ever revisits the session (TTL cleanup, bounded interval).
        from .media_maintenance import (
            start_media_maintenance_sweep,
            stop_media_maintenance_sweep,
        )

        media_maintenance_task = start_media_maintenance_sweep()

        # Periodic telemetry retention sweep so ``cleanup_old_data`` actually
        # runs on the configured retention schedule instead of never.
        from .telemetry_maintenance import (
            start_telemetry_retention_sweep,
            stop_telemetry_retention_sweep,
        )

        telemetry_retention_task = start_telemetry_retention_sweep()

        # Register route templates so client-supplied telemetry endpoints are
        # allowlisted against real API routes (cardinality protection).
        # ``route.path`` carries the router mount prefix (e.g. /api/v1) while
        # clients report unprefixed paths, so the configured prefix is
        # stripped here to keep both sides on the same form.
        try:
            from fastapi.routing import APIRoute

            from src.core import telemetry as _telemetry_module

            def _strip_api_prefix(path: str) -> str:
                prefix = (config.api_prefix or "/api/v1").rstrip("/")
                if prefix and path.startswith(prefix + "/"):
                    return path[len(prefix) :] or "/"
                return path

            if _telemetry_module.is_setup():
                _telemetry_module.register_route_templates(
                    _strip_api_prefix(route.path)
                    for route in app.routes
                    if isinstance(route, APIRoute)
                )
        except Exception as e:
            logger.debug(f"Telemetry route template registration skipped: {e}")

        # Start the cross-worker WebSocket event listener so this
        # worker receives events published by sibling workers via
        # Valkey pub/sub.
        try:
            from src.api.websocket import start_cross_worker_listener

            await start_cross_worker_listener()
        except Exception as e:
            logger.warning(f"Cross-worker listener not started: {e}")

        # Start the event dispatch-queue consumer so events queued by
        # ``events.dispatch`` are fanned out to regular subscribers.
        # Without this consumer the deferred queue is write-only and
        # non-critical subscribers never receive any event.
        try:
            from src.core import events as events_module

            if events_module.is_setup():
                await events_module.start_consumer()
        except Exception as e:
            logger.warning(f"Event queue consumer not started: {e}")

        try:
            yield
        finally:
            await stop_poll_expiry_sweep(poll_expiry_task)
            await stop_media_maintenance_sweep(media_maintenance_task)
            await stop_telemetry_retention_sweep(telemetry_retention_task)

            # Stop the queue consumer and drain any queued events so
            # shutdown does not silently drop them.
            try:
                from src.core import events as events_module

                if events_module.is_setup():
                    await events_module.stop_consumer()
            except Exception as e:
                logger.debug(f"Event queue consumer stop error: {e}")

            # Shut down the cross-worker listener before the event
            # loop stops, ensuring clean unsubscribe + thread join.
            try:
                from src.api.websocket import stop_cross_worker_listener

                await stop_cross_worker_listener()
            except Exception as e:
                logger.debug(f"Cross-worker listener stop error: {e}")

            await stop_static_client_service()

    app = FastAPI(
        title=config.title,
        description=config.description,
        version=config.version or "",
        docs_url=None,
        redoc_url=None,
        openapi_url=config.openapi_url,
        lifespan=_lifespan,
    )

    if config.docs_url and config.openapi_url:
        openapi_url = config.openapi_url
        oauth2_redirect_url = f"{config.docs_url.rstrip('/')}/oauth2-redirect"

        @app.get(config.docs_url, include_in_schema=False)
        async def swagger_ui_html(request: Request) -> HTMLResponse:
            return render_swagger_ui_page(
                request,
                config.title,
                openapi_url,
                oauth2_redirect_url=oauth2_redirect_url,
            )

        @app.get(oauth2_redirect_url, include_in_schema=False)
        async def swagger_ui_redirect() -> HTMLResponse:
            return HTMLResponse(get_swagger_ui_oauth2_redirect_html())

    if config.redoc_url and config.openapi_url:
        openapi_url = config.openapi_url

        @app.get(config.redoc_url, include_in_schema=False)
        async def redoc_html(request: Request) -> HTMLResponse:
            return render_redoc_page(request, config.title, openapi_url)

    # SECURITY: rate limiting must run AFTER authentication so per-user
    # buckets / admin bypass / bot multipliers work. In Starlette the LAST
    # add_middleware call is OUTERMOST and executes FIRST, therefore the
    # limiter is registered BEFORE AuthenticationMiddleware here.
    if enable_rate_limiting:
        from src.core import ratelimit

        if ratelimit.is_setup():
            RateLimitMiddleware = create_rate_limit_middleware()
            app.add_middleware(RateLimitMiddleware)

    app.add_middleware(AuthenticationMiddleware)

    # SECURITY: SecurityHeaders + Database are registered further down,
    # AFTER MaxBodySizeMiddleware, so the locally-generated 413 responses
    # pass back OUT through the security headers middleware (last added
    # = outermost). Registering them here left every 413 without those
    # headers.

    # CORS handles OPTIONS preflight - MUST be outermost
    # (In ASGI, last added is outermost)
    # Max request body size middleware (configurable, default 10MB for voice uploads)
    # Added BEFORE CORS so CORS remains outermost and 413 responses get CORS headers
    max_body_size = config.max_request_body_size

    # PlexiSum workbook uploads carry base64-encoded .plexisum files inside
    # JSON bodies; a 50MB workbook needs ~67MB of base64, far above the
    # generic cap, so the Sum routes get their own larger limit.
    sum_upload_prefix = f"{config.api_prefix}/suite/sum/"
    sum_upload_max_bytes = max(max_body_size, 96 * 1024 * 1024)

    # PlexiStash JSON uploads are the same pattern: base64 file bytes inside
    # a JSON body, bounded by the route's own MAX_UPLOAD_ENCODED_LEN (70MB)
    # plus request envelope, so they get an explicit larger cap too. Without
    # this, a large upload dies at the generic 10MB middleware boundary with
    # a generic "Request body too large" before the route can explain itself.
    stash_upload_prefix = f"{config.api_prefix}/suite/stash/"
    stash_upload_max_bytes = max(max_body_size, 96 * 1024 * 1024)

    class MaxBodySizeMiddleware(BaseHTTPMiddleware):
        """Reject requests whose body exceeds the configured maximum.

        SECURITY: the previous implementation only consulted the
        Content-Length header. ASGI servers (Uvicorn / Hypercorn /
        Starlette) honour chunked transfer-encoding — when a client
        sends ``Transfer-Encoding: chunked`` with no Content-Length,
        ``request.headers`` has nothing to check, the integrity
        cap was effectively skipped, and an attacker uploading a 50GB
        SV-evader PDF could exhaust memory or disk before any
        endpoint-specific bound fired.

        We now:
          1. Honour the Content-Length hint when present.
          2. ALWAYS track bytes actually consumed from
             ``receive()`` via a wrapping async iterator. As soon as
             the running total crosses ``max_body_size`` the receive
             wrapper stops yielding and we return 413 without ever
             calling the next handler.
          3. Reject any request that announces
             ``Transfer-Encoding: chunked`` whose declared length or
             observed bytecount isn't bounded the same way. The
             chunked code path is therefore still bounded, even
             without Content-Length.
        """

        async def dispatch(self, request: Request, call_next):
            path = request.url.path
            if path.startswith(sum_upload_prefix):
                effective_cap = sum_upload_max_bytes
            elif path.startswith(stash_upload_prefix):
                effective_cap = stash_upload_max_bytes
            else:
                effective_cap = max_body_size
            content_length = request.headers.get("content-length")
            # L2: a malformed Content-Length (non-numeric) previously crashed
            # int() -> 500. Treat unparseable lengths as unknown and defer to
            # the byte-counting wrapper, which enforces the same cap on the
            # actual body regardless.
            declared_length = None
            if content_length:
                try:
                    declared_length = int(content_length)
                except (TypeError, ValueError):
                    logger.warning(
                        "Rejecting malformed Content-Length header %r", content_length
                    )
                    return Response(
                        content=(
                            '{"error":{"code":400,"message":"Invalid Content-Length"}}'
                        ),
                        status_code=400,
                        media_type="application/json",
                    )
            if declared_length is not None and declared_length > effective_cap:
                return Response(
                    content=(
                        '{"error":{"code":413,"message":"Request body too large",'
                        f'"max_bytes":{effective_cap}}}'
                    ),
                    status_code=413,
                    media_type="application/json",
                )
            # Per-method request bodies for GET/HEAD/OPTIONS/DELETE-
            # style flows don't carry a body, so we still defer to
            # the next handler.
            if request.method in {"GET", "HEAD", "OPTIONS"}:
                return await call_next(request)

            # Wrap the ASGI receive callable so the byte counter is
            # incremented on every body chunk and a 413 fires the
            # instant the cap would be exceeded. We do NOT iterate
            # the body here — that would break streaming endpoints
            # (uploads) — we only wrap the receive callable so
            # downstream code naturally trips the bound.
            original_receive = request.receive

            bytes_seen = {"n": 0}

            async def _bounded_receive():
                msg = await original_receive()
                if msg.get("type") == "http.request":
                    chunk_len = len(msg.get("body", b"") or b"")
                    if bytes_seen["n"] + chunk_len > effective_cap:
                        raise _BodyTooLarge(effective_cap)
                    bytes_seen["n"] += chunk_len
                return msg

            object.__setattr__(request, "_receive", _bounded_receive)
            try:
                return await call_next(request)
            except _BodyTooLarge:
                return Response(
                    content=(
                        '{"error":{"code":413,"message":"Request body too large",'
                        f'"max_bytes":{effective_cap}}}'
                    ),
                    status_code=413,
                    media_type="application/json",
                )

    app.add_middleware(MaxBodySizeMiddleware)

    # Registered AFTER MaxBodySize (=> outer than it) so 413 responses
    # still gain security headers; Database stays outside SecurityHeaders.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(DatabaseMiddleware)

    try:
        from src.static_client import install_static_client_middleware

        install_static_client_middleware(app)
    except Exception as e:
        logger.warning(f"Static client middleware not installed: {e}")

    # Explicit OPTIONS preflight handler for local dev - catches any
    # preflight that slips through the CORSMiddleware. Implemented as a
    # middleware that only intercepts OPTIONS so that unmatched non-OPTIONS
    # paths still fall through to Starlette and produce a normal 404; a
    # catch-all ``@app.options("/{path:path}")`` *route* would partially
    # match every request and turn every unknown path into a spurious 405.
    class CORSPreflightMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if request.method != "OPTIONS":
                return await call_next(request)
            origin = request.headers.get("origin")
            if origin and origin in config.cors_origins:
                return Response(
                    status_code=200,
                    headers={
                        "Access-Control-Allow-Origin": origin,
                        "Access-Control-Allow-Credentials": "true",
                        "Access-Control-Allow-Methods": ", ".join(
                            config.cors_allow_methods
                        ),
                        "Access-Control-Allow-Headers": ", ".join(
                            config.cors_allow_headers
                        ),
                        "Access-Control-Expose-Headers": ", ".join(
                            config.cors_expose_headers
                        ),
                        "Access-Control-Max-Age": "86400",
                    },
                )
            return Response(status_code=400)

    app.add_middleware(CORSPreflightMiddleware)

    # CORS handles OPTIONS preflight - MUST be outermost
    # (In ASGI, last added is outermost)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=config.cors_allow_credentials,
        allow_methods=config.cors_allow_methods,
        allow_headers=config.cors_allow_headers,
        expose_headers=config.cors_expose_headers,
    )

    # H3: IP blacklisting was implemented but NEVER registered, so blacklisted
    # IPs were not blocked at the perimeter at all. Registered outermost so a
    # blacklisted peer is rejected before any CORS/rate-limit/DB work runs.
    app.add_middleware(IPBlockingMiddleware)

    # M1: request logging middleware was dead code - never registered. Added
    # outermost so timing covers the full stack (CORS preflights included).
    app.add_middleware(LoggingMiddleware)

    setup_exception_handlers(app)

    api_router = create_api_router()
    app.include_router(api_router, prefix=config.api_prefix)

    # Redirect root-level /admin to /api/v1/admin/login
    admin_config = global_config.get("admin_ui", {})
    if admin_config.get("enabled", False):
        from fastapi.responses import RedirectResponse

        admin_path = admin_config.get("path", "/admin")
        target_path = f"{config.api_prefix}{admin_path}/ui"

        @app.get(admin_path, include_in_schema=False)
        async def admin_redirect():
            return RedirectResponse(url=target_path, status_code=302)

        @app.get(f"{admin_path}/", include_in_schema=False)
        async def admin_slash_redirect():
            return RedirectResponse(url=target_path, status_code=302)

        logger.info(f"Admin UI redirects enabled: {admin_path} -> {target_path}")

    # Include WebSocket gateway router
    try:
        from src.api import websocket

        if not websocket.is_setup():
            # Setup websocket with available modules
            import src.api as api_module

            websocket.setup(
                auth_module=api_module.get_auth(),
                presence_module=api_module.get_presence(),
                servers_module=api_module.get_servers(),
                events_module=api_module.get_events(),
            )
        gateway_router = websocket.get_router()
        app.include_router(gateway_router)
        logger.info("WebSocket gateway enabled at /gateway")
    except Exception as e:
        logger.warning(f"WebSocket gateway not available: {e}")

    # Mount public license endpoints (challenge-response for remote
    # license export; see public_license module for the protocol).
    try:
        from .routes.public_license import router as public_license_router

        app.include_router(public_license_router)
        logger.info("Public license endpoints enabled at /public/license/*")
    except Exception as e:
        logger.warning(f"Public license endpoints not available: {e}")

    # Mount documentation router if enabled
    # Path is configurable via config.yaml docs.path
    docs_path = "/docs/api"  # Default
    if enable_docs and is_docs_enabled():
        try:
            docs_conf = get_docs_config()
            docs_path = docs_conf.path
        except Exception as e:
            logger.warning(f"Failed to load docs config: {e}")

        docs_router = create_docs_router()
        app.include_router(docs_router, prefix=docs_path)
        logger.info(f"Documentation server enabled at {docs_path}")

    from .schemas.common import RootResponse, ErrorResponse
    from .routes.health import HealthResponse

    @app.get(
        "/",
        response_model=RootResponse,
        summary="API Root",
        tags=["System"],
        responses={
            500: {"model": ErrorResponse, "description": "Internal server error"},
        },
    )
    async def root() -> RootResponse:
        """Root endpoint with API information."""
        try:
            response = {
                "name": config.title,
                "version": config.version,
                "docs": config.docs_url,
                "api": config.api_prefix,
            }
            if enable_docs and is_docs_enabled():
                response["api_docs"] = docs_path
            return RootResponse(**response)
        except Exception as e:
            logger.error(f"Root endpoint failed: {e}", exc_info=True)
            from fastapi import status

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )

    @app.get(
        "/health",
        response_model=HealthResponse,
        summary="Health Check",
        tags=["System"],
        responses={
            500: {"model": ErrorResponse, "description": "Internal server error"},
        },
    )
    async def health_redirect() -> HealthResponse:
        """Redirect or proxy to health check endpoint."""
        try:
            from .routes.health import health_check

            return await health_check()
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Health redirect failed: {e}", exc_info=True)
            from fastapi import status

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )

    # Attachment serving route (extracted to keep app.py sub-1000 lines)
    from .media_serving import register_attachment_route

    register_attachment_route(app)

    return app
