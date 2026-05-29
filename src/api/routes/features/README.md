# Feature Expansion Routes

## Module Layout

| File | Routes | Description |
|------|--------|-------------|
| `__init__.py` | — | Aggregator: combines all sub-routers into the exported `feature_expansion_router` |
| `common.py` | — | Shared helpers: `raise_bad_request`, `raise_forbidden`, `parse_id`, `call_or_raise` |
| `bookmarks.py` | POST/DELETE/GET `/bookmarks` | Per-user message bookmarks |
| `scheduled_messages.py` | POST/GET/DELETE `/scheduled-messages` | Schedule messages for future delivery |
| `forwarding.py` | POST `/forward` | Forward messages between conversations |
| `voice.py` | POST `/voice-messages`, `/voice-messages/upload` | Voice message attachments |
| `profiles.py` | GET `/users/{id}/profile`, PATCH `/users/@me/profile` | User profiles (bio, status, social links) |
| `push.py` | POST/DELETE `/push/tokens` | Mobile push notification tokens |
| `last_chat.py` | PUT/GET `/users/@me/last-chat`, GET `/users/@me/recent-chats` | Last active conversation |
| `slowmode.py` | PUT/GET `/channels/{id}/slowmode`, `/threads/{id}/slowmode` | Channel and thread slowmode |
| `audit_logs.py` | GET `/users/@me/audit-logs` | User-facing audit log |
| `reports.py` | POST `/reports/enhanced`, PATCH `/reports/{id}/status`, GET `/reports/{id}` | Enhanced reporting |
| `onboarding.py` | GET `/onboarding/presets`, POST `/onboarding/apply-preset` | Onboarding wizard presets |
| `tier_features.py` | Various `/admin/users/.../features`, `/users/@me/features` | User features, badges, tiers (migrated from `features.py`) |

## Architecture

Each sub-module defines its own `APIRouter` instance with route decorators.
The `__init__.py` imports all sub-routers and combines them into a single
`feature_expansion_router` via `include_router()`.

The parent `routes/__init__.py` now imports `feature_expansion_router` from
this package instead of the old `feature_routes.py`.

Error handling is standardised via `common.py` helpers:
- `parse_id(value, field)` — parse string to int or raise 400
- `call_or_raise(func, *args, **kwargs)` — catch `ValueError` -> 400, `PermissionError` -> 403
- `raise_bad_request`, `raise_forbidden`, `raise_not_found`, `raise_internal` — convenience one-liners

## Usage

```python
# In a route handler (e.g., bookmarks.py)
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

## Error Handling

- `parse_id()` raises `HTTPException(400)` if the value cannot be converted to `int`.
- `call_or_raise()` catches `ValueError` -> `HTTPException(400)` and `PermissionError` -> `HTTPException(403)`.
- Individual route handlers may raise additional HTTP exceptions directly via the `raise_*` helpers.
- All responses use FastAPI's standard `HTTPException` for consistency.

## Dependencies
- FastAPI `APIRouter` and `Depends`.
- Core feature/user/profile modules for business logic.
- Auth middleware for current-user resolution.
