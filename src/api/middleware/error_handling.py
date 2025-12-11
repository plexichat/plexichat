"""
Error handling middleware - Convert exceptions to HTTP responses.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

import utils.config as config


ERROR_MAPPINGS = {
    "NotFoundError": 404,
    "AccessDeniedError": 403,
    "PermissionDeniedError": 403,
    "InvalidCredentialsError": 401,
    "TokenExpiredError": 401,
    "TokenInvalidError": 401,
    "AccountLockedError": 403,
    "UserExistsError": 409,
    "AlreadyExistsError": 409,
    "ExistsError": 409,
    "ValidationError": 400,
    "InvalidError": 400,
    "LimitError": 400,
    "BlockedError": 403,
    "LockedError": 423,
    "ArchivedError": 410,
}


def get_status_code_for_exception(exc: Exception) -> int:
    """Determine HTTP status code based on exception type."""
    exc_name = type(exc).__name__

    for pattern, code in ERROR_MAPPINGS.items():
        if pattern in exc_name:
            return code

    return 500


def format_error_response(code: int, message: str) -> dict:
    """Format error response in standard format."""
    return {"error": {"code": code, "message": message}}


def _get_cors_headers(request: Request) -> dict:
    """Get CORS headers for error responses."""
    try:
        api_conf = config.get("api", {})
        cors_origins = api_conf.get("cors_origins", ["*"])
    except Exception:
        cors_origins = ["*"]

    origin = request.headers.get("origin", "")

    # Check if origin is allowed
    if "*" in cors_origins or origin in cors_origins:
        return {
            "Access-Control-Allow-Origin": origin if origin else "*",
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept",
        }
    return {}


def setup_exception_handlers(app: FastAPI):
    """Setup exception handlers for the FastAPI application."""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        """Handle HTTP exceptions."""
        detail = exc.detail
        headers = _get_cors_headers(request)

        if isinstance(detail, dict) and "error" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail, headers=headers)

        return JSONResponse(
            status_code=exc.status_code,
            content=format_error_response(exc.status_code, str(detail)),
            headers=headers
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """Handle request validation errors."""
        errors = exc.errors()
        headers = _get_cors_headers(request)

        if errors:
            first_error = errors[0]
            loc = first_error.get("loc", [])
            msg = first_error.get("msg", "Validation error")
            field = loc[-1] if loc else "unknown"
            message = f"Invalid {field}: {msg}"
        else:
            message = "Request validation failed"

        return JSONResponse(
            status_code=400,
            content=format_error_response(400, message),
            headers=headers
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        """Handle all other exceptions."""
        status_code = get_status_code_for_exception(exc)
        message = str(exc) if status_code != 500 else "Internal server error"
        headers = _get_cors_headers(request)

        return JSONResponse(
            status_code=status_code,
            content=format_error_response(status_code, message),
            headers=headers
        )
