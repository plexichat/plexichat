# API Routes

FastAPI route handlers for all API endpoints.

## Endpoints

- `auth.py` - Authentication (login, register, 2FA)
- `channels.py` - Channel management
- `docs.py` - API documentation
- `health.py` - Health check endpoints
- `messages.py` - Message CRUD operations
- `presence.py` - User presence/status
- `reactions.py` - Message reactions
- `relationships.py` - Friends and blocking
- `servers.py` - Server/guild management
- `settings.py` - User settings
- `users.py` - User profile management
- `version.py` - API version info
- `webhooks.py` - Webhook management

## Usage

```python
from src.api.routes import create_api_router

router = create_api_router()
app.include_router(router, prefix="/api")
```
