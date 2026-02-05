"""
FastAPI application factory - Creates and configures the API application.
"""

from fastapi import FastAPI, Request, HTTPException, status, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, RedirectResponse
import sys
import time
import re
import unicodedata
from typing import Optional

import utils.config as global_config
import utils.logger as logger
from .config import get_api_config
from .routes import create_api_router, create_docs_router, is_docs_enabled
from .routes.docs import get_docs_config

# Local in-memory cache for media file metadata to avoid redundant DB lookups
_media_metadata_cache = {} # {filename: metadata_row}

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
        docs_url=config.docs_url,
        redoc_url=config.redoc_url,
        openapi_url=config.openapi_url,
    )

    # Middleware order matters! They run in REVERSE order of addition.
    # Desired execution: Logging -> CORS -> Auth -> IP Blocking -> RateLimit -> app

    if enable_rate_limiting:
        from src.core import ratelimit

        if ratelimit.is_setup():
            RateLimitMiddleware = create_rate_limit_middleware()
            app.add_middleware(RateLimitMiddleware)

    app.add_middleware(IPBlockingMiddleware)
    app.add_middleware(DatabaseMiddleware)
    app.add_middleware(AuthenticationMiddleware)
    app.add_middleware(ErrorHandlingMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    # CORS handles OPTIONS preflight
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=config.cors_allow_credentials,
        allow_methods=config.cors_allow_methods,
        allow_headers=config.cors_allow_headers,
    )

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

    # Mount documentation router if enabled
    # Path is configurable via config.yaml docs.path
    docs_path = "/docs/api"  # Default
    if enable_docs and is_docs_enabled():
        try:
            docs_conf = get_docs_config()
            docs_path = docs_conf.path
        except Exception:
            pass

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
        "/api/v1/media/attachments/{filename}",
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

        try:
            # --- Signature Verification (Bypass Auth) ---
            media = api_module.get_media()
            if not media:
                 raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Media module unavailable"}})

            is_signed = False
            try:
                # Check if URL has valid signature
                sig_valid, _ = media.verify_signed_url(str(request.url))
                if sig_valid:
                    is_signed = True
                    logger.debug(f"Access granted via signed URL: {filename}")
            except Exception:
                pass

            # --- Authentication (Required if no valid signature or internal) ---
            is_internal = request.scope.get("state", {}).get("is_internal", False)
            
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
                        detail={"error": {"code": 401, "message": "Authentication required"}}
                    )

                # Verify token
                auth = api_module.get_auth()
                if not auth:
                    raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Auth module unavailable"}})
                
                try:
                    # Try validating as standard user token
                    # TODO: Implement optional binding validation in AuthManager if needed
                    token_info = auth.verify_token(token)
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
                             logger.warning(f"Attachment auth failed for {filename}: {e}")
                             raise HTTPException(status_code=401, detail={"error": {"code": 401, "message": "Invalid token"}})
                    except Exception:
                        raise HTTPException(status_code=401, detail={"error": {"code": 401, "message": "Invalid token"}})

            # --- File Lookup ---
            db = api_module.get_db()
            if not db:
                raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Database module unavailable"}})

            # Find file in database (check cache first)
            row = _media_metadata_cache.get(filename)
            if not row:
                row = db.fetch_one(
                    "SELECT id, storage_backend, storage_path, content_type, original_filename FROM media_files WHERE filename = ? AND deleted = 0",
                    (filename,)
                )
                if row:
                    _media_metadata_cache[filename] = row
            
            if not row:
                raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "File not found"}})

            file_id = row["id"]
            backend = row["storage_backend"]
            original_filename = row["original_filename"]
            download = request.query_params.get("download", "0") == "1"

            # --- File Retrieval: Stream via media module ---
            # We always stream through the server now to ensure:
            # 1. Decryption works correctly (EncryptedStorage handles this)
            # 2. Authentication works (we use our own session/cookie)
            # 3. CORS/AccessDenied issues with direct S3 redirects are avoided
            try:
                start_time = time.perf_counter()
                # Optimized: Use metadata we already have to skip DB lookup
                # stream, size, ct = media.get_file_stream(file_id)
                stream, size, ct = media.get_file_stream_optimized(
                    row["storage_path"], 
                    row["content_type"], 
                    row["storage_backend"]
                )
                
                # Sanitize original filename for Content-Disposition
                # This ensures downloads retain their initial names while being safe for headers
                def sanitize_header_filename(name):
                    # Normalize and remove non-ascii
                    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
                    # Replace potentially problematic characters
                    name = re.sub(r'[^\w\.\-]', '_', name)
                    return name or "attachment"

                safe_name = sanitize_header_filename(original_filename)
                
                # Generate ETag from file_id and size (simple but effective for immutable files)
                etag = f'"{file_id}-{size}"'
                
                # Check If-None-Match
                if_none_match = request.headers.get("If-None-Match")
                if if_none_match and (if_none_match == etag or if_none_match == etag.replace('"', '')):
                     return Response(status_code=304)

                headers = {
                    "Content-Length": str(size),
                    "Content-Disposition": f'attachment; filename="{safe_name}"' if download else "inline",
                    "Cache-Control": "private, max-age=3600",
                    "ETag": etag
                }
                
                # Add CORS headers specifically for media to avoid policy blocks
                # We must use the specific Origin if credentials (cookies/auth) are used
                origin = request.headers.get("Origin")
                allowed_origins = config.cors_origins
                
                # If no Origin header (direct browser access), we don't need CORS
                if not origin:
                    headers["Access-Control-Allow-Origin"] = "*"
                else:
                    # Determine if this specific origin is allowed
                    is_allowed = False
                    if isinstance(allowed_origins, list):
                        if "*" in allowed_origins or origin in allowed_origins:
                            is_allowed = True
                    elif isinstance(allowed_origins, str):
                        if allowed_origins == "*" or allowed_origins == origin:
                            is_allowed = True
                    
                    if is_allowed:
                        # CRITICAL: If credentials are true, Origin MUST NOT be '*'
                        headers["Access-Control-Allow-Origin"] = origin
                        headers["Access-Control-Allow-Credentials"] = "true"
                    else:
                        # Fallback for unauthorized origins
                        headers["Access-Control-Allow-Origin"] = "null"
                
                headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
                headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-Requested-With, Accept, Origin"
                
                # Log duration after the stream is acquired
                duration = (time.perf_counter() - start_time) * 1000
                if duration > 1000:
                    logger.warning(f"Slow file retrieval for {filename}: {duration:.1f}ms (backend: {backend})")
                
                return StreamingResponse(
                    stream,
                    media_type=ct,
                    headers=headers
                )
            except Exception as e:
                logger.error(f"Generic retrieval failed for {filename}: {e}")
                raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Retrieval failed"}})

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Unexpected error serving attachment {filename}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Internal server error"}})

    return app