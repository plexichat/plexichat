# API Middleware

Request processing middleware for the PlexiChat API.

## Components

### Authentication (`authentication.py`)

Token validation and user extraction middleware.

**Key Functions:**
- `get_current_user` - Dependency that extracts and validates the auth token
- `get_optional_user` - Same as above but returns None if no token
- `TokenInfo` - Dataclass containing user info from token

**Usage:**
```python
from src.api.middleware.authentication import get_current_user, TokenInfo

@router.get("/protected")
async def handler(current_user: TokenInfo = Depends(get_current_user)):
    user_id = current_user.user_id
    username = current_user.username
```

**TokenInfo Fields:**
- `user_id` - User's snowflake ID
- `username` - Username
- `session_id` - Session ID (if session token)
- `account_type` - Account type (user, bot)
- `permissions` - Permission dict

### Rate Limiting (`rate_limiting.py`)

Rate limit enforcement middleware.

**Features:**
- Per-route rate limits
- Per-user and per-IP limits
- Token bucket and sliding window algorithms
- Rate limit headers in responses

**Headers Added:**
- `X-RateLimit-Limit` - Maximum requests
- `X-RateLimit-Remaining` - Remaining requests
- `X-RateLimit-Reset` - Reset timestamp
- `X-RateLimit-Bucket` - Bucket identifier

### Error Handling (`error_handling.py`)

Global exception handling middleware.

**Features:**
- Consistent error response format
- Exception logging
- HTTP status code mapping

**Error Format:**
```json
{
  "error": {
    "code": 404,
    "message": "Resource not found"
  }
}
```

### Logging (`logging.py`)

Request logging middleware.

**Features:**
- Request/response logging
- Timing information
- Client IP logging (configurable)
- Log level configuration

## Middleware Order

Middleware is applied in this order:
1. CORS
2. Logging
3. Error Handling
4. Rate Limiting
5. Authentication (per-route via Depends)
