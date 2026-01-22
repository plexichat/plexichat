"""
FastAPI application factory - Creates and configures the API application.
"""

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import sys
from pathlib import Path

import utils.config as global_config
from .config import get_api_config
from .middleware import (
    AuthenticationMiddleware,
    setup_exception_handlers,
    ErrorHandlingMiddleware,
    LoggingMiddleware,
    SecurityHeadersMiddleware,
    create_rate_limit_middleware,
    IPBlockingMiddleware,
)
from .routes import create_api_router, create_docs_router, is_docs_enabled, admin_router
from .routes.docs import get_docs_config

import utils.logger as logger


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

    # Serve uploaded media files (requires authentication)
    from fastapi.responses import FileResponse
    from fastapi import status
    from pathlib import Path
    from typing import Optional
    from src.core.auth.models import TokenInfo

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
        from fastapi.responses import FileResponse, RedirectResponse
        from fastapi import status
        import src.api as api_module

        try:
            # --- Authentication ---
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
            # Verify token
            auth = api_module.get_auth()
            if not auth:
                raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Auth module unavailable"}})
            
            try:
                # Try validating as standard user token first
                token_info: TokenInfo = auth.verify_token(token)
                if not token_info:
                    raise ValueError("Invalid user token")
            except Exception:
                # Fallback: Check if it's a valid ADMIN token
                # This allows the Admin Dashboard to load user attachments without 401 errors
                try:
                    import src.api as api_module
                    admin = api_module.get_admin()
                    if admin and admin.validate_session(token):
                        # Valid admin session - allow access
                        pass 
                    else:
                         raise HTTPException(status_code=401, detail={"error": {"code": 401, "message": "Invalid token"}})
                except Exception:
                    raise HTTPException(status_code=401, detail={"error": {"code": 401, "message": "Invalid token"}})

            # --- File Lookup ---
            db = api_module.get_db()
            media = api_module.get_media()
            if not db or not media:
                raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Core modules unavailable"}})

            # Find file in database
            row = db.fetch_one(
                "SELECT id, storage_backend, storage_path, content_type FROM media_files WHERE filename = ? AND deleted = 0",
                (filename,)
            )
            
            if not row:
                raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "File not found"}})

            file_id = row["id"]
            backend = row["storage_backend"]
            storage_path = row["storage_path"]
            content_type = row["content_type"]
            download = request.query_params.get("download", "0") == "1"

            # --- S3 Storage Optimization: Redirect to signed URL ---
            if backend == "s3":
                try:
                    # Generate a short-lived signed URL (5 minutes)
                    # For S3, we MUST include the content-disposition in the signature
                    params = {}
                    if download:
                        params["ResponseContentDisposition"] = f"attachment; filename={filename}"
                    
                    signed = media.sign_url(file_id, expires_in=300, params=params)
                    return RedirectResponse(signed.url, status_code=status.HTTP_302_FOUND)
                except Exception as e:
                    logger.error(f"Failed to generate signed URL for {filename}: {e}")
                    # Fallback to streaming if signing fails (slower but works)
                    pass

            # --- Local/Fallback Storage: Stream via FileResponse ---
            # Get actual file path for local storage
            if backend == "local":
                # Use base path from config
                media_config = global_config.get("media", {})
                base_path = Path(media_config.get("local_path", "uploads"))
                file_path = base_path / storage_path
                
                if not file_path.exists():
                    logger.error(f"Local file missing for database record: {storage_path}")
                    raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "File not found"}})
                
                if download:
                    return FileResponse(file_path, filename=filename, media_type="application/octet-stream")
                return FileResponse(file_path, media_type=content_type)

            # --- Generic Fallback: Retrieve data via media module (slowest) ---
            try:
                data, ct = media.get_file_data(file_id)
                from fastapi import Response
                response = Response(
                    content=data,
                    media_type=ct,
                    headers={
                        "Content-Disposition": f"attachment; filename={filename}" if download else "inline"
                    }
                )
                return response
            except Exception as e:
                logger.error(f"Generic retrieval failed for {filename}: {e}")
                raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Retrieval failed"}})

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Unexpected error serving attachment {filename}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Internal server error"}})

    return app
