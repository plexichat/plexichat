"""
FastAPI application factory - Creates and configures the API application.
"""

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import sys

from .config import get_api_config
from .middleware import (
    AuthenticationMiddleware,
    setup_exception_handlers,
    ErrorHandlingMiddleware,
    LoggingMiddleware,
    create_rate_limit_middleware,
    IPBlockingMiddleware,
)
from .routes import create_api_router, create_docs_router, is_docs_enabled
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
            401: {"model": ErrorResponse, "description": "Not authenticated"},
            403: {"model": ErrorResponse, "description": "Access denied"},
            404: {"model": ErrorResponse, "description": "File not found"},
            500: {"model": ErrorResponse, "description": "Internal server error"},
        },
    )
    async def serve_attachment(filename: str, request: Request) -> FileResponse:
        """Serve uploaded attachment files. Requires authentication via Authorization header or cookie."""
        try:
            # Try Authorization header first (preferred for API calls)
            auth_header: Optional[str] = request.headers.get("Authorization")
            token: Optional[str] = None

            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header[7:]

            # Fallback to cookie-based auth for <img> tags and direct browser access
            # This is safe because cookies are HttpOnly and not exposed to JS
            if not token:
                token = request.cookies.get("plexichat_token")

            if not token:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "error": {
                            "code": 401,
                            "message": "Authentication required. Use Authorization header or cookie.",
                        }
                    },
                )

            # Verify token
            try:
                import src.api as api_module

                auth = api_module.get_auth()
                if auth:
                    token_info: TokenInfo = auth.verify_token(token)
                    if not token_info:
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail={
                                "error": {
                                    "code": 401,
                                    "message": "Invalid or expired token",
                                }
                            },
                        )
                else:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail={
                            "error": {
                                "code": 500,
                                "message": "Auth module not available",
                            }
                        },
                    )
            except HTTPException:
                raise
            except Exception as e:
                logger.error(
                    f"Token verification failed for media access: {e}", exc_info=True
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "error": {"code": 401, "message": "Invalid or expired token"}
                    },
                )

            # Serve the file
            media_dir = Path.home() / ".plexichat" / "media" / "attachments"
            file_path = media_dir / filename

            # Security: prevent path traversal
            try:
                file_path = file_path.resolve()
                if not str(file_path).startswith(str(media_dir.resolve())):
                    logger.warning(
                        f"Blocked path traversal attempt for filename: {filename}"
                    )
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail={"error": {"code": 403, "message": "Access denied"}},
                    )
            except Exception as e:
                logger.error(f"Path resolution error for media: {e}", exc_info=True)
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"error": {"code": 403, "message": "Access denied"}},
                )

            if not file_path.exists():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"error": {"code": 404, "message": "File not found"}},
                )

            # Check if download is requested
            download = request.query_params.get("download", "0") == "1"

            # Determine media type for proper content-type header
            import mimetypes

            media_type = (
                mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
            )

            if download:
                response = FileResponse(
                    file_path, filename=filename, media_type="application/octet-stream"
                )
            else:
                response = FileResponse(file_path, media_type=media_type)

            # Add CORS headers to prevent OpaqueResponseBlocking
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = (
                "Authorization, Content-Type"
            )
            # Cache for 24 hours
            response.headers["Cache-Control"] = "public, max-age=86400"

            return response
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error serving attachment {filename}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )

    return app
