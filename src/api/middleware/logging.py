"""
Logging middleware - Request/response logging with accurate timing.

This middleware measures the COMPLETE request duration from when the request
is received until the response body is fully sent to the client.
"""

import time
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.types import ASGIApp, Receive, Send, Scope, Message


def _get_logger():
    """Get the logger module dynamically to ensure we use the configured instance."""
    try:
        import utils.logger as logger
        return logger
    except ImportError:
        return None


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
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        # Extract request info
        method = scope.get("method", "UNKNOWN")
        path = scope.get("path", "/")
        
        # Get client IP
        client = scope.get("client")
        client_ip = client[0] if client else "unknown"
        
        # Check if we should log this request
        should_log = path not in SKIP_PATHS
        
        if not should_log:
            await self.app(scope, receive, send)
            return
        
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
                    
                    log_msg = f"{method} {path} - {status_code} - {duration_ms:.2f}ms - {client_ip}"
                    
                    if status_code >= 500:
                        _log_error(log_msg)
                    elif status_code >= 400:
                        _log_warning(log_msg)
                    else:
                        _log_info(log_msg)
            
            await send(message)
        
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as e:
            # Log error with timing
            end_time = time.perf_counter()
            duration_ms = (end_time - start_time) * 1000
            _log_error(f"{method} {path} - ERROR - {duration_ms:.2f}ms - {client_ip} - {str(e)}")
            raise
