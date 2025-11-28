"""
FastAPI application factory - Creates and configures the API application.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_api_config
from .middleware import AuthenticationMiddleware, setup_exception_handlers, LoggingMiddleware
from .routes import create_api_router


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.
    
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
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=config.cors_allow_credentials,
        allow_methods=config.cors_allow_methods,
        allow_headers=config.cors_allow_headers,
    )
    
    app.add_middleware(AuthenticationMiddleware)
    app.add_middleware(LoggingMiddleware)
    
    setup_exception_handlers(app)
    
    api_router = create_api_router()
    app.include_router(api_router, prefix=config.api_prefix)
    
    @app.get("/")
    async def root():
        """Root endpoint with API information."""
        return {
            "name": config.title,
            "version": config.version,
            "docs": config.docs_url,
            "api": config.api_prefix,
        }
    
    return app
