# API Routes

FastAPI route handlers for all API endpoints.

## Structure

Each file contains routes for a specific resource domain:

| File | Prefix | Description |
|------|--------|-------------|
| `auth.py` | `/auth` | Authentication and session management |
| `users.py` | `/users` | User profile management |
| `servers.py` | `/servers` | Server/guild management |
| `channels.py` | `/channels` | Channel management |
| `messages.py` | `/channels/{id}/messages` | Messaging |
| `reactions.py` | `/channels/{id}/messages/{id}/reactions` | Message reactions |
| `relationships.py` | `/relationships` | Friends and blocks |
| `presence.py` | `/users` | User presence/status |
| `webhooks.py` | `/webhooks` | Webhook management |
| `avatars.py` | `/avatars` | Avatar and icon management |
| `emojis.py` | `/servers/{id}/emojis` | Custom emoji |
| `settings.py` | `/settings` | User settings sync |
| `features.py` | `/admin`, `/users/@me/features` | User features/badges |
| `health.py` | `/health` | Health check |
| `version.py` | `/version`, `/status` | Version and status |
| `qr.py` | `/qr` | Local QR code generation |
| `docs.py` | `/docs/api` | Documentation server |
| `media.py` | `/media` | Media file serving |
| `admin.py` | `/admin` | Admin endpoints |
| `feedback.py` | `/feedback` | User feedback |
| `telemetry.py` | `/telemetry` | Client telemetry |
| `notifications.py` | `/notifications` | Push notifications |
| `voice.py` | `/voice` | Voice channel signaling |

## Route Pattern

Each route file follows a consistent pattern:

```python
from fastapi import APIRouter, HTTPException, Depends
from src.api.middleware.authentication import get_current_user, TokenInfo

router = APIRouter()

@router.get("/endpoint")
async def handler(current_user: TokenInfo = Depends(get_current_user)):
    """Endpoint description."""
    # Implementation
    pass
```

## Authentication

Routes use dependency injection for authentication:

```python
from src.api.middleware.authentication import get_current_user, TokenInfo

# Require authentication
@router.get("/protected")
async def protected(current_user: TokenInfo = Depends(get_current_user)):
    pass

# Optional authentication
from src.api.middleware.authentication import get_optional_user

@router.get("/optional")
async def optional(current_user: Optional[TokenInfo] = Depends(get_optional_user)):
    pass
```

## Error Handling

Routes raise HTTPException with consistent error format:

```python
raise HTTPException(
    status_code=404,
    detail={"error": {"code": 404, "message": "Resource not found"}}
)
```

## WebSocket Events

Routes that modify data dispatch WebSocket events:

```python
from src.api.websocket import get_dispatcher, is_setup
from src.core.events.models import Event
from src.core.events.types import EventType

if is_setup():
    dispatcher = get_dispatcher()
    event = Event(event_type=EventType.MESSAGE_CREATE, data=response)
    await dispatcher.dispatch_event(event, user_ids)
```
