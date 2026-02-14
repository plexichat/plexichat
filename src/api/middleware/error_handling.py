"""
Error handling middleware - Convert exceptions to HTTP responses.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from starlette.types import ASGIApp, Receive, Send, Scope

import traceback
import utils.config as config
try:
    from utils.logger import sanitize_log_message
except ImportError:
    # Fallback if common-utils is not synced
    def sanitize_log_message(message: str) -> str:
        return message


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
    "AttachmentLimitError": 400,
    "RateLimitError": 429,
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
    # Sanitize message to prevent PII leakage
    safe_message = sanitize_log_message(message)
    return {"error": {"code": code, "message": safe_message}}


def _get_cors_headers(request: Request) -> dict:
    """Get CORS headers for error responses."""
    try:
        api_conf = config.get("api", {})
        cors_origins = api_conf.get("cors_origins", ["*"])
        allow_wildcard = api_conf.get("allow_wildcard_cors", False)
    except Exception:
        cors_origins = ["*"]
        allow_wildcard = False

    origin = request.headers.get("origin", "")

    # Check if origin is allowed
    is_allowed = False
    if "*" in cors_origins:
        if allow_wildcard or not origin:
            is_allowed = True
        else:
            # If wildcard is not allowed for this origin, check if origin is explicitly allowed
            is_allowed = origin in cors_origins
    elif origin in cors_origins:
        is_allowed = True

    if is_allowed:
        return {
            "Access-Control-Allow-Origin": origin if origin else "*",
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept, X-Custom-Header",
        }
    return {}


class ErrorHandlingMiddleware:
    """Middleware to catch all exceptions and return JSON responses."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        try:
            await self.app(scope, receive, send)
        except Exception as exc:
            # Determine status code and message
            status_code = get_status_code_for_exception(exc)

            # Don't leak details for 500 errors in production
            debug = False
            try:
                debug = config.get("api", {}).get("debug", False)
            except Exception:
                pass

            message = str(exc)
            if status_code == 500 and not debug:
                message = "Internal server error"

            # Check for self-test debug mode
            request = Request(scope)
            include_traceback = False

            # Only allow traceback capture if:
            # 1. Config says it's enabled
            # 2. Request is from localhost
            # 3. Secure internal secret is present and matches
            import src.api as api
            selftest_config = config.get("selftest", {})
            is_local = (
                request.client.host in ("127.0.0.1", "::1") if request.client else False
            )

            internal_secret = api.get_internal_secret()
            provided_secret = request.headers.get("X-Plexichat-Internal-Secret")
            is_selftest = (
                internal_secret is not None
                and provided_secret == internal_secret
                and is_local
            )

            if selftest_config.get("capture_stack_traces", True) and is_selftest:
                if request.headers.get("X-Plexichat-SelfTest-Debug") == "true":
                    include_traceback = True

            # Get CORS headers
            headers = _get_cors_headers(request)

            # Create response content
            content = format_error_response(status_code, message)
            if include_traceback:
                content["error"]["traceback"] = "".join(traceback.format_exc())

            # Create response
            response = JSONResponse(
                status_code=status_code, content=content, headers=headers
            )

            await response(scope, receive, send)


def setup_exception_handlers(app: FastAPI):
    """Setup exception handlers for the FastAPI application."""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        """Handle HTTP exceptions."""
        detail = exc.detail
        headers = _get_cors_headers(request)
        
        # Don't leak details for 500 errors in production
        debug = False
        try:
            debug = config.get("api", {}).get("debug", False)
        except Exception:
            pass

        if isinstance(detail, dict) and "error" in detail:
            # Mask internal server error messages
            if exc.status_code == 500 and not debug:
                detail["error"]["message"] = "Internal server error"
            else:
                # Sanitize whatever message is there
                msg = detail["error"].get("message")
                if msg and isinstance(msg, str):
                    detail["error"]["message"] = sanitize_log_message(msg)
            
            return JSONResponse(
                status_code=exc.status_code, content=detail, headers=headers
            )

        return JSONResponse(
            status_code=exc.status_code,
            content=format_error_response(exc.status_code, str(detail)),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ):
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
            headers=headers,
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
            headers=headers,
        )
