# Feature expansion routes

This directory aggregates feature-expansion endpoints for bookmarks, scheduled messages, forwarding, voice, profiles, and related features.

## Overview

The `features` package splits the former monolithic `feature_routes.py` into focused sub-modules. Each sub-module defines its own `APIRouter`. `__init__.py` combines the sub-routers into a single `feature_expansion_router` (tagged `Feature Expansion`) that the parent `routes` package mounts. A separate `features_router` from `tier_features.py` continues to serve user tiers, badges, and admin feature management.

All handlers use shared helpers in `common.py` to validate input and map domain errors to HTTP responses.

## File structure

| File | Routes | Description |
|------|--------|-------------|
| `__init__.py` | — | Aggregates sub-routers into `feature_expansion_router`; re-exports `features_router` from `tier_features.py`. |
| `common.py` | — | Helpers: `raise_bad_request`, `raise_forbidden`, `raise_not_found`, `raise_internal`, `parse_id`, `call_or_raise`. |
| `bookmarks.py` | `POST /bookmarks`, `DELETE /bookmarks/{message_id}`, `GET /bookmarks` | Per-user message bookmarks. Dispatches `MESSAGE_BOOKMARK_ADD` and `MESSAGE_BOOKMARK_REMOVE` events to the caller. |
| `scheduled_messages.py` | `POST /scheduled-messages`, `GET /scheduled-messages`, `DELETE /scheduled-messages/{id}` | Schedule messages for future delivery. |
| `forwarding.py` | `POST /forward` | Forward messages between conversations. |
| `voice.py` | `POST /voice-messages`, `POST /voice-messages/upload` | Voice message attachments. |
| `profiles.py` | `GET /users/{id}/profile`, `PATCH /users/@me/profile` | User profiles: bio, status, and social links. |
| `push.py` | `POST /push/tokens`, `DELETE /push/tokens` | Mobile push notification tokens. |
| `last_chat.py` | `PUT /users/@me/last-chat`, `GET /users/@me/last-chat`, `GET /users/@me/recent-chats` | Last active conversation and recent chats. |
| `slowmode.py` | `PUT /channels/{id}/slowmode`, `GET /channels/{id}/slowmode`, `PUT /threads/{id}/slowmode`, `GET /threads/{id}/slowmode` | Channel and thread slowmode. |
| `audit_logs.py` | `GET /users/@me/audit-logs` | User-facing audit log. |
| `reports.py` | `POST /reports/enhanced`, `PATCH /reports/{id}/status`, `GET /reports/{id}` | Enhanced reporting. |
| `onboarding.py` | `GET /onboarding/presets`, `POST /onboarding/apply-preset` | Onboarding wizard presets. |
| `tier_features.py` | `GET /admin/users/{id}/features`, `PUT /admin/users/{id}/features`, `GET /users/@me/features`, related badge and tier paths | User features, badges, and tiers. Migrated from the former `features.py`. |
| `protocol.py` | — | Protocol definitions for feature services. |

## Architecture

Each sub-module creates an `APIRouter` at module scope and decorates handler functions directly:

```python
# bookmarks.py
router = APIRouter()

@router.post("/bookmarks", summary="Bookmark a message")
async def add_bookmark(body: BookmarkRequest, current_user: TokenInfo = Depends(get_current_user)):
    message_id = parse_id(body.message_id, "message ID")
    result = call_or_raise(bookmark_svc.add_bookmark, user_id=current_user.user_id, message_id=message_id, ...)
    return {"success": True, "bookmark": result}
```

`__init__.py` then includes every sub-router:

```python
feature_expansion_router = APIRouter(tags=["Feature Expansion"])
feature_expansion_router.include_router(bookmarks_router)
feature_expansion_router.include_router(scheduled_messages_router)
# ...
```

## Shared helpers

`common.py` standardizes error handling:

| Helper | Behavior |
|--------|----------|
| `parse_id(value, field)` | Validates that `value` is a positive decimal string and returns `int`. Raises `HTTPException(400)` if invalid. Rejects booleans and non-decimal input. |
| `call_or_raise(func, *args, **kwargs)` | Calls `func` and maps `ValueError` to `400` and `PermissionError` to `403`. Maps participant errors to `Not a participant in this conversation`. |
| `raise_bad_request(message)` | Raises `HTTPException(400)` with a structured error body. |
| `raise_forbidden(message)` | Raises `HTTPException(403)`. |
| `raise_not_found(message)` | Raises `HTTPException(404)`. |
| `raise_internal(message)` | Raises `HTTPException(500)`. |

## Example

```python
from .common import parse_id, call_or_raise

@router.post("/bookmarks")
async def create_bookmark(message_id: str, user = Depends(get_current_user)):
    msg_id = parse_id(message_id, "message_id")
    result = call_or_raise(
        feature_manager.create_bookmark,
        user.id, msg_id
    )
    return {"bookmark": result}
```

Error responses use the standard FastAPI shape:

```json
{ "detail": { "error": { "code": 400, "message": "Invalid message ID" } } }
```

## Dependencies

* FastAPI `APIRouter` and `Depends`.
* Core feature, messaging, and profile modules for business logic.
* Authentication middleware (`get_current_user`) for resolving the current user.
* Database handle from `src.api.get_db()` for services that require it.
* WebSocket dispatcher for bookmark and related events.

## Notes

* All routes require authentication through `get_current_user` unless otherwise noted.
* Bookmark add and remove handlers dispatch events asynchronously with `asyncio.create_task` so the HTTP response does not block on WebSocket delivery.
* `tier_features.py` is the source of `features_router`. Do not confuse it with `feature_expansion_router`; the former serves tier and badge management, the latter serves the feature-expansion endpoints listed above.
