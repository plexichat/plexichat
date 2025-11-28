"""
Logging middleware - Request/response logging.
"""

import time
import os
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")
if common_utils_path not in sys.path:
    sys.path.append(common_utils_path)

import utils.logger as logger

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


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
                logger.error(log_msg)
            elif status_code >= 400:
                logger.warning(log_msg)
            else:
                logger.debug(log_msg)
            
            return response
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(f"{method} {path} - ERROR - {duration_ms:.2f}ms - {client_ip} - {str(e)}")
            raise
