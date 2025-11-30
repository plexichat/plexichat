# API Middleware

FastAPI middleware components for request processing.

## Components

- `authentication.py` - JWT token validation and user extraction
- `error_handling.py` - Global exception handlers
- `logging.py` - Request/response logging
- `rate_limiting.py` - Rate limit enforcement middleware

## Usage

```python
from src.api.middleware import (
    AuthenticationMiddleware,
    get_current_user,
    setup_exception_handlers,
    LoggingMiddleware,
    RateLimitMiddleware,
)
```
