"""
FastAPI application factory - Creates and configures the API application.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_api_config
from .middleware import (
    AuthenticationMiddleware,
    setup_exception_handlers,
    LoggingMiddleware,
    create_rate_limit_middleware,
)
from .routes import create_api_router, create_docs_router, is_docs_enabled
from .routes.docs import get_docs_config

import utils.logger as logger
import utils.config as app_config


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
    
    app = FastAPI(
        title=config.title,
        description=config.description,
        version=config.version,
        docs_url=config.docs_url,
        redoc_url=config.redoc_url,
        openapi_url=config.openapi_url,
    )
    
    # Middleware order matters! They run in REVERSE order of addition.
    # So we add them in this order: Logging -> Auth -> RateLimit -> CORS
    # Which means they execute: CORS -> RateLimit -> Auth -> Logging
    
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(AuthenticationMiddleware)
    
    if enable_rate_limiting:
        from src.core import ratelimit
        if ratelimit.is_setup():
            RateLimitMiddleware = create_rate_limit_middleware()
            app.add_middleware(RateLimitMiddleware)
    
    # CORS must be added LAST so it runs FIRST (handles OPTIONS preflight)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=config.cors_allow_credentials,
        allow_methods=config.cors_allow_methods,
        allow_headers=config.cors_allow_headers,
    )
    
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
    
    @app.get("/")
    async def root():
        """Root endpoint with API information."""
        response = {
            "name": config.title,
            "version": config.version,
            "docs": config.docs_url,
            "api": config.api_prefix,
        }
        if enable_docs and is_docs_enabled():
            response["api_docs"] = docs_path
        return response
    
    # Serve uploaded media files (requires authentication)
    from fastapi.responses import FileResponse
    from fastapi import Request, HTTPException
    from pathlib import Path
    
    @app.get("/api/v1/media/attachments/{filename}")
    async def serve_attachment(filename: str, request: Request):
        """Serve uploaded attachment files. Requires authentication."""
        # Check for authentication token
        auth_header = request.headers.get("Authorization")
        token = None
        
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]
        elif "token" in request.query_params:
            # Allow token in query param for direct image/video embeds
            token = request.query_params.get("token")
        
        if not token:
            raise HTTPException(
                status_code=401, 
                detail={"error": {"code": 401, "message": "Authentication required"}}
            )
        
        # Verify token
        try:
            import src.api as api_module
            auth = api_module.get_auth()
            if auth:
                token_info = auth.verify_token(token)
                if not token_info:
                    raise HTTPException(
                        status_code=401, 
                        detail={"error": {"code": 401, "message": "Invalid or expired token"}}
                    )
            else:
                raise HTTPException(
                    status_code=500, 
                    detail={"error": {"code": 500, "message": "Auth module not available"}}
                )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=401, 
                detail={"error": {"code": 401, "message": "Invalid or expired token"}}
            )
        
        # Serve the file
        media_dir = Path.home() / ".plexichat" / "media" / "attachments"
        file_path = media_dir / filename
        
        # Security: prevent path traversal
        try:
            file_path = file_path.resolve()
            if not str(file_path).startswith(str(media_dir.resolve())):
                raise HTTPException(
                    status_code=403, 
                    detail={"error": {"code": 403, "message": "Access denied"}}
                )
        except Exception:
            raise HTTPException(
                status_code=403, 
                detail={"error": {"code": 403, "message": "Access denied"}}
            )
        
        if not file_path.exists():
            raise HTTPException(
                status_code=404, 
                detail={"error": {"code": 404, "message": "File not found"}}
            )
        
        # Check if download is requested
        download = request.query_params.get("download", "0") == "1"
        
        if download:
            return FileResponse(
                file_path, 
                filename=filename,
                media_type="application/octet-stream"
            )
        
        return FileResponse(file_path)
    
    return app
