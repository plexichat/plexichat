"""
FastAPI application factory - Creates and configures the API application.
"""

from fastapi import FastAPI, Request, HTTPException, status, Response
from fastapi.openapi.docs import get_swagger_ui_oauth2_redirect_html
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
import sys
import re
import unicodedata
import threading
from typing import Optional, AsyncGenerator

import utils.config as global_config
import utils.logger as logger
from .config import get_api_config
from .routes import create_api_router, create_docs_router, is_docs_enabled
from .routes.docs import get_docs_config, render_redoc_page, render_swagger_ui_page

# Local in-memory cache for media file metadata to avoid redundant DB lookups
from collections import OrderedDict


class _LRUCache:
    """Simple thread-safe bounded LRU cache that behaves like a dict for get/setitem/contains."""

    def __init__(self, maxsize: int = 1024):
        self._cache: OrderedDict = OrderedDict()
        self._maxsize = maxsize
        self._lock = threading.Lock()

    def get(self, key, default=None):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            return default

    def __setitem__(self, key, value):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = value
            if len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)

    def __contains__(self, key):
        with self._lock:
            return key in self._cache


_media_metadata_cache = _LRUCache(maxsize=1024)


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
    from .middleware import (
        AuthenticationMiddleware,
        setup_exception_handlers,
        ErrorHandlingMiddleware,
        LoggingMiddleware,
        SecurityHeadersMiddleware,
        create_rate_limit_middleware,
        IPBlockingMiddleware,
        DatabaseMiddleware,
    )

    config = get_api_config()
    # Debug CORS config
    if "pytest" in sys.modules:
        logger.debug(f"CORS Origins: {config.cors_origins}")
        logger.debug(f"CORS Headers: {config.cors_allow_headers}")

    app = FastAPI(
        title=config.title,
        description=config.description,
        version=config.version or "",
        docs_url=None,
        redoc_url=None,
        openapi_url=config.openapi_url,
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

    if enable_rate_limiting:
        from src.core import ratelimit

        if ratelimit.is_setup():
            RateLimitMiddleware = create_rate_limit_middleware()
            app.add_middleware(RateLimitMiddleware)

    app.add_middleware(AuthenticationMiddleware)
    app.add_middleware(IPBlockingMiddleware)
    app.add_middleware(ErrorHandlingMiddleware)
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(DatabaseMiddleware)

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
        app.include_router(docs_router, prefix=docs_path, tags=["Documentation"])
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

    @app.get(
        "/api/v1/media/attachments/{filename:path}",
        summary="Serve attachment",
        tags=["Media"],
        responses={
            200: {
                "content": {"application/octet-stream": {}},
                "description": "The attachment file content",
            },
            307: {"description": "Redirect to S3 storage"},
            401: {"model": ErrorResponse, "description": "Not authenticated"},
            403: {"model": ErrorResponse, "description": "Access denied"},
            404: {"model": ErrorResponse, "description": "File not found"},
            500: {"model": ErrorResponse, "description": "Internal server error"},
        },
    )
    async def serve_attachment(filename: str, request: Request):
        """Serve uploaded attachment files. Handles local and S3 storage with redirect optimization."""
        import src.api as api_module

        # SECURITY: Prevent path traversal. Filenames must be a single segment.
        # If a traversal attempt is made, reject immediately before touching media/auth.
        if (
            not filename
            or ".." in filename
            or "/" in filename
            or "\\" in filename
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {"code": 400, "message": "Invalid filename"}
                },
            )

        try:
            token = None
            token_info = None
            # --- Signature Verification (Bypass Auth) ---
            media = api_module.get_media()
            if not media:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error": {"code": 500, "message": "Media module unavailable"}
                    },
                )

            is_signed = False
            try:
                # Check if URL has valid signature
                # Extract path and query for consistent verification with sign_url
                url_to_verify = request.url.path
                if request.url.query:
                    url_to_verify += f"?{request.url.query}"

                # SECURITY: Try to get user_id from session/cookies to verify signed URL ownership
                current_user_id = None
                from .middleware.authentication import get_token_info
                token = request.headers.get("Authorization")
                if token and token.startswith("Bearer "):
                    token = token[7:]
                else:
                    token = request.cookies.get("plexichat_token")
                
                if token:
                    token_info = await get_token_info(token)
                    if token_info:
                        current_user_id = token_info.user_id

                sig_valid, _ = await media.verify_signed_url(url_to_verify, current_user_id=current_user_id)
                if sig_valid:
                    is_signed = True
                    logger.debug(f"Access granted via signed URL: {filename}")
            except Exception as e:
                logger.debug(f"Signed URL verification failed for {filename}: {e}")

            # --- Authentication (Required if no valid signature or internal) ---
            is_internal = request.scope.get("state", {}).get("is_internal", False)

            if is_internal and not is_signed:
                logger.info(f"Auth: Internal service access to media file: {filename}")

            if not is_signed and not is_internal:
                # Try Authorization header first (preferred for API calls)
                auth_header: Optional[str] = request.headers.get("Authorization")
                token: Optional[str] = None

                if auth_header and auth_header.startswith("Bearer "):
                    token = auth_header[7:]

                # Fallback to cookie-based auth for <img> tags and direct browser access
                if not token:
                    token = request.cookies.get("plexichat_token")

                if not token:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail={
                            "error": {"code": 401, "message": "Authentication required"}
                        },
                    )

                # Verify token
                auth = api_module.get_auth()
                if not auth:
                    raise HTTPException(
                        status_code=500,
                        detail={
                            "error": {"code": 500, "message": "Auth module unavailable"}
                        },
                    )

                try:
                    # SECURITY: Use hardened utility to extract client IP from trusted proxies.
                    from src.utils.net import get_client_ip
                    client_ip = get_client_ip(request)
                    
                    user_agent = request.headers.get("User-Agent")
                    token_info = auth.verify_token(token, client_ip, user_agent)
                    if not token_info:
                        raise ValueError("Invalid user token")
                except Exception as e:
                    # Fallback: Check if it's a valid ADMIN token
                    # This allows the Admin Dashboard to load user attachments without 401 errors
                    try:
                        admin = api_module.get_admin()
                        if admin and admin.validate_session(token):
                            # Valid admin session - allow access
                            pass
                        else:
                            logger.warning(
                                f"Attachment auth failed for {filename}: {e}"
                            )
                            raise HTTPException(
                                status_code=401,
                                detail={
                                    "error": {"code": 401, "message": "Invalid token"}
                                },
                            )
                    except Exception:
                        raise HTTPException(
                            status_code=401,
                            detail={"error": {"code": 401, "message": "Invalid token"}},
                        )

            # --- Authorization Check ---
            # Verify user has permission to access this specific file (uploader or participant)
            if not is_signed and not is_internal:
                # Admins and internal services can bypass granular checks
                is_privileged = False
                try:
                    admin_mod = api_module.get_admin()
                    if admin_mod and admin_mod.validate_session(token):
                        is_privileged = True
                except Exception as e:
                    logger.debug(f"Admin session validation failed for {filename}: {e}")

                if not is_privileged:
                    # Resolve user_id from token info
                    if token_info is None:
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail={
                                "error": {
                                    "code": 401,
                                    "message": "Authentication required",
                                }
                            },
                        )
                    uid_raw = getattr(token_info, "user_id", None)
                    if uid_raw and not media.check_file_access(filename, int(uid_raw)):
                        logger.warning(
                            f"Unauthorized attachment access blocked: file={filename}, user={uid_raw}"
                        )
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail={
                                "error": {
                                    "code": 403,
                                    "message": "Access denied to this file",
                                }
                            },
                        )

            # --- File Lookup ---
            db = api_module.get_db()
            if not db:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error": {"code": 500, "message": "Database module unavailable"}
                    },
                )

            # Find file in database (check cache first)
            row = _media_metadata_cache.get(filename)
            if not row:
                logger.debug(f"Media cache miss for {filename}, querying database...")
                row = db.fetch_one(
                    "SELECT id, storage_backend, storage_path, content_type, original_filename FROM media_files WHERE filename = ? AND deleted = 0",
                    (filename,),
                )
                if row:
                    logger.debug(
                        f"Found media file in DB: {filename} (ID: {row['id']})"
                    )
                    _media_metadata_cache[filename] = row
                else:
                    logger.warning(f"Media file NOT FOUND in DB: {filename}")

            if not row:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "File not found"}},
                )

            file_id = row["id"]
            original_filename = row["original_filename"]
            download = request.query_params.get("download", "0") == "1"

            # --- File Retrieval: Stream via media module ---
            # We always stream through the server now to ensure:
            # 1. Decryption works correctly (EncryptedStorage handles this)
            # 2. Authentication works (we use our own session/cookie)
            # 3. CORS/AccessDenied issues with direct S3 redirects are avoided
            try:
                # Optimized: Use metadata we already have to skip DB lookup
                # stream, size, ct = media.get_file_stream(file_id)
                stream, size, ct = media.get_file_stream_optimized(
                    row["storage_path"], row["content_type"], row["storage_backend"]
                )

                # Sanitize original filename for Content-Disposition
                # This ensures downloads retain their initial names while being safe for headers
                def sanitize_header_filename(name):
                    # Normalize and remove non-ascii
                    name = (
                        unicodedata.normalize("NFKD", name)
                        .encode("ascii", "ignore")
                        .decode("ascii")
                    )
                    # Replace potentially problematic characters
                    name = re.sub(r"[^\w\.\-]", "_", name)
                    return name or "attachment"

                safe_name = sanitize_header_filename(original_filename)

                # Generate ETag from file_id and size
                etag = f'"{file_id}-{size}"'

                # Check If-None-Match
                if_none_match = request.headers.get("If-None-Match")
                if if_none_match and (
                    if_none_match == etag or if_none_match == etag.replace('"', "")
                ):
                    return Response(status_code=304)

                import inspect

                is_seekable = (
                    not inspect.isgenerator(stream)
                    and hasattr(stream, "seek")
                    and hasattr(stream, "tell")
                )
                range_header = request.headers.get("Range") if is_seekable else None
                start = 0
                end = size - 1
                is_partial = False

                if range_header and range_header.startswith("bytes="):
                    try:
                        range_val = range_header.replace("bytes=", "")
                        if "-" in range_val:
                            r_start, r_end = range_val.split("-")
                            if r_start:
                                start = int(r_start)
                            if r_end:
                                end = int(r_end)

                        # Constrain range
                        end = min(end, size - 1)
                        if start <= end:
                            is_partial = True
                        else:
                            start = 0
                    except Exception:
                        is_partial = False

                content_length = end - start + 1
                status_code = 206 if is_partial else 200

                # Add CORS headers manually for maximum reliability
                origin = request.headers.get("Origin")
                cors_headers = {}
                allowed_origins = config.cors_origins
                if origin and (origin in allowed_origins or "*" in allowed_origins):
                    cors_headers["Access-Control-Allow-Origin"] = origin
                    cors_headers["Access-Control-Allow-Credentials"] = "true"
                elif "*" in allowed_origins:
                    cors_headers["Access-Control-Allow-Origin"] = "*"

                headers = {
                    "Content-Disposition": f'attachment; filename="{safe_name}"'
                    if download
                    else "inline",
                    "Cache-Control": "private, max-age=3600",
                    "ETag": etag,
                    "Accept-Ranges": "bytes",
                    "Vary": "Origin, Range",
                    "Access-Control-Expose-Headers": "Content-Range, Accept-Ranges, Content-Length, ETag",
                    **cors_headers,
                }
                if is_seekable:
                    headers["Content-Length"] = str(content_length)

                if is_partial:
                    headers["Content-Range"] = f"bytes {start}-{end}/{size}"

                # Helper to correctly slice any stream (file or generator)
                async def get_response_iterator(
                    s, skip, limit
                ) -> AsyncGenerator[bytes, None]:
                    import inspect
                    from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

                    count = 0
                    yielded = 0

                    try:
                        if (
                            not inspect.isgenerator(s)
                            and hasattr(s, "read")
                            and hasattr(s, "seek")
                        ):
                            if skip > 0:
                                await run_in_threadpool(s.seek, skip)
                            while yielded < limit:
                                chunk = await run_in_threadpool(
                                    s.read, min(65536, limit - yielded)
                                )
                                if not chunk:
                                    break
                                yield chunk
                                yielded += len(chunk)
                            if hasattr(s, "close"):
                                await run_in_threadpool(s.close)
                        else:
                            async for chunk in iterate_in_threadpool(s):
                                chunk_len = len(chunk)
                                if count + chunk_len <= skip:
                                    count += chunk_len
                                    continue
                                chunk_start = max(0, skip - count)
                                chunk_end = min(
                                    chunk_len, chunk_start + (limit - yielded)
                                )
                                if chunk_start < chunk_end:
                                    part = chunk[chunk_start:chunk_end]
                                    yield part
                                    yielded += len(part)
                                count += chunk_len
                                if yielded >= limit:
                                    break
                    except Exception as e:
                        logger.error(f"Media stream interrupted for {filename}: {e}")

                response_iterator = get_response_iterator(
                    stream, start, content_length
                )
                return StreamingResponse(
                    response_iterator,
                    status_code=status_code,
                    media_type=ct,
                    headers=headers,
                )
            except Exception as e:
                logger.error(f"Generic retrieval failed for {filename}: {e}")
                raise HTTPException(
                    status_code=500,
                    detail={"error": {"code": 500, "message": "Retrieval failed"}},
                )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error serving attachment {filename}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )

    return app
