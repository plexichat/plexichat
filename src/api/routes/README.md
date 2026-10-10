# Routes

This directory contains FastAPI route handlers for all HTTP endpoints. Each module owns a resource domain and its URL prefix.

## Overview

Routes are organized by resource, not by HTTP method. You add endpoints by defining handlers in the relevant module and including the router in `src.api.routes.create_api_router()`. The main router is mounted at `config.api_prefix` (default `/api/v1`) in `src.api.app.create_app()`.

## Route table

| Module | Prefix | Description |
|--------|--------|-------------|
| `auth/` | `/auth` | Login, registration, session management, and email verification |
| `users/` | `/users` | User profile and account management |
| `servers/` | `/servers` | Guild creation, membership, and server settings |
| `channels/` | `/channels` | Channel management, permissions, and invites |
| `messages/` | `/channels/{channelId}/messages` | Message create, read, update, and delete |
| `reactions.py` | `/channels/{channelId}/messages/{messageId}/reactions` | Message reactions |
| `relationships/` | `/relationships` | Friends, blocks, and relationship requests |
| `presence.py` | `/users/@me/presence`, `/users/{userId}/presence` | User presence and status |
| `threads.py` | (various) | Thread creation and management |
| `webhooks.py` | `/webhooks` | Webhook create, execute, and manage |
| `applications.py` | `/applications` | OAuth application management |
| `bots.py` | `/bots` | Bot account management |
| `avatars.py` | `/avatars` | User and server avatar uploads |
| `media.py` | `/media` | Media file serving and attachment routes |
| `emojis.py` | `/servers/{serverId}/emojis` | Custom emoji management |
| `stickers.py` | `/stickers` | Sticker management |
| `soundboard.py` | `/servers/{serverId}/soundboard` | Server sound library |
| `settings.py` | `/users/@me/settings` | User settings synchronization |
| `features/` | `/features`, `/admin`, `/users/@me/features` | User tiers, badges, and feature expansion |
| `search.py` | `/search` | Full-text search |
| `polls.py` | (poll endpoints) | Poll create, vote, and expiry |
| `artifacts.py` | `/artifacts` | Artifact CRUD and inline transcript emission |
| `artifacts_export.py`, `artifacts_helpers.py` | (helpers) | Export and helper logic for artifacts |
| `federation.py` | `/federation` | Cross-instance artifact federation (PlexiJoin transport) |
| `join.py` | `/Join` | Client-facing federation surface and contract |
| `suite/registry.py` | (PlexiSuite) | PlexiSuite registry and universal-home feed |
| `suite/stash.py` | `/suite/stash` | PlexiStash metadata and authorization |
| `suite/sum.py` | `/suite/sum` | PlexiSum workbook handling |
| `notifications.py` | `/users/@me/notifications` | Push notification management |
| `voice.py` | `/voice` | Voice channel signaling |
| `reports.py` | `/reports` | Message and user reporting |
| `feedback.py` | `/feedback` | User feedback submission |
| `telemetry.py` | `/telemetry` | Client telemetry and `csp-report` |
| `health.py` | `/health` | Health check |
| `version.py` | `/version`, `/status` | Version negotiation and status |
| `capabilities.py` | `/capabilities` | Server capability discovery |
| `config.py` | `/config` | Public client configuration |
| `qr.py` | `/qr` | Local QR code generation |
| `help.py` | `/help`, `/robots.txt` | Help system and robots file |
| `docs/`, `docs.py` | `/docs/api` | Documentation server |
| `admin/` | `/admin` | Administrative endpoints (see `admin/README.md`) |

Admin sub-routes are documented separately in `admin/README.md`. Sub-packages such as `auth/`, `users/`, `servers/`, `channels/`, `messages/`, and `relationships/` contain their own routers and schemas.

## Route pattern

Each route module follows the same conventions:

```python
from fastapi import APIRouter, HTTPException, Depends
from src.api.middleware.authentication import get_current_user, TokenInfo

router = APIRouter()

@router.get("/endpoint")
async def handler(current_user: TokenInfo = Depends(get_current_user)):
    """Return the requested resource."""
    # Implementation
    ...
```

Router prefixes are applied in `routes/__init__.py` when the sub-router is included, not inside the sub-module itself. For example:

```python
# routes/__init__.py
api_router.include_router(users_router, prefix="/users")
api_router.include_router(auth_router, prefix="/auth")
```

## Authentication

Use dependency injection to require or optionally accept authentication:

```python
from typing import Optional
from fastapi import Depends
from src.api.middleware.authentication import get_current_user, get_optional_user, TokenInfo

# Require a valid token; raises 401 or 403 when checks fail
@router.get("/protected")
async def protected(current_user: TokenInfo = Depends(get_current_user)):
    user_id = current_user.user_id
    ...

# Accept a token when present; returns None for anonymous requests
@router.get("/optional")
async def optional(current_user: Optional[TokenInfo] = Depends(get_optional_user)):
    if current_user is None:
        ...
```

`get_current_user` enforces account status before returning: locked accounts receive `403`, accounts with `deletion_status=frozen` receive `403`, and accounts with `force_username_change` are restricted to `GET` or `PATCH /users/@me`, `POST /auth/logout`, and `/avatars/*`.

## Error handling

Raise `HTTPException` with the standard error envelope. The error handling middleware in `src.api.middleware` preserves this shape and adds CORS headers:

```python
from fastapi import HTTPException

raise HTTPException(
    status_code=404,
    detail={"error": {"code": 404, "message": "Resource not found"}},
)
```

Validation errors are raised automatically by Pydantic and are returned as `400` with the same envelope.

## WebSocket events

Routes that mutate data dispatch real-time events to connected clients through the gateway. Check that the gateway is initialized before dispatching:

```python
from src.api.websocket import get_dispatcher, is_setup
from src.core.events.models import Event
from src.core.events.types import EventType

if is_setup():
    dispatcher = get_dispatcher()
    event = Event(event_type=EventType.MESSAGE_CREATE, data=response)
    await dispatcher.dispatch_event(event, user_ids)
```

Cross-worker delivery is handled by the Valkey pub/sub listener started in `app.py` lifespan.

## Create a new route

1.  Create a new module or sub-package under `routes/` (for example, `routes/widgets.py` or `routes/widgets/`).
2.  Define a `router = APIRouter()` and add handlers with standard FastAPI decorators.
3.  Add the router to `create_api_router()` in `routes/__init__.py` with the appropriate prefix.
4.  Add Pydantic schemas under `src.api.schemas` if the endpoint needs request or response validation.
5.  Register telemetry route templates if you need client-reported endpoint validation (see `app.py` lifespan).

## Related documentation

*   `../README.md` — API module overview and application factory
*   `../middleware/README.md` — authentication and middleware order
*   `admin/README.md` — administrative endpoints
*   `../schemas/` — request and response models
