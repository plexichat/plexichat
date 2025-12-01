"""
Logging middleware - Request/response logging.
"""

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


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


class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for logging HTTP requests and responses."""
    
    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request and log details."""
        start_time = time.time()
        
        method = request.method
        path = request.url.path
        client_ip = request.client.host if request.client else "unknown"
        
        try:
            response = await call_next(request)
            
            duration_ms = (time.time() - start_time) * 1000
            status_code = response.status_code
            
            log_msg = f"{method} {path} - {status_code} - {duration_ms:.2f}ms - {client_ip}"
            
            if status_code >= 500:
                _log_error(log_msg)
            elif status_code >= 400:
                _log_warning(log_msg)
            else:
                _log_debug(log_msg)
            
            return response
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            _log_error(f"{method} {path} - ERROR - {duration_ms:.2f}ms - {client_ip} - {str(e)}")
            raise
