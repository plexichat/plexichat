# API schemas

Define Pydantic models that validate requests and serialize responses for the Plexichat API.

## Structure

| File | Purpose |
|------|---------|
| `common.py` | Shared types: `SnowflakeID`, `ErrorResponse`, `PaginationParams`, `PaginatedResponse`, `SuccessResponse`, `RootResponse` |
| `auth.py` | Authentication: `RegisterRequest`, `LoginRequest`, `LoginResponse`, `TokenResponse`, `UserResponse` |
| `users.py` | User profiles: `UserUpdateRequest`, `UserDeletionRequest`, `UserPublicResponse`, `UserAvatarResponse` |
| `servers.py` | Servers and server channels: `ServerCreateRequest`, `ServerResponse`, `ChannelCreateRequest`, `ChannelResponse`, `MemberResponse`, `RoleResponse`, `BanResponse`, automod types |
| `channels.py` | DM and notes channels: `DMChannelResponse`, `DMChannelCreateRequest`, `NotesChannelResponse`, invites, attachments |
| `messages.py` | Messages: `MessageCreateRequest`, `MessageUpdateRequest`, `MessageResponse`, `MessagingSettingsResponse`, read receipts, transcripts |
| `reactions.py` | Reactions |
| `relationships.py` | Friends and blocks |
| `presence.py` | Presence and status |
| `webhooks.py` | Webhooks |
| `version.py` | Version negotiation |
| `dsar.py` | Data export (GDPR): `DSARRequestBody`, `DSARRequestResponse`, `DSARRequestListResponse` |
| `artifacts.py` | Build artifacts |
| `avatars.py` | Avatar uploads |
| `config.py` | Server configuration |
| `emojis.py` | Custom emojis |
| `features.py` | Feature flags |
| `feedback.py` | User feedback |
| `media.py` | Media handling |
| `notifications.py` | Push and in-app notifications |
| `polls.py` | Polls |
| `reports.py` | Moderation reports |
| `search.py` | Search |
| `settings.py` | User settings |
| `soundboard.py` | Soundboard |
| `stash.py` | Stash / bookmarks |
| `stickers.py` | Stickers |
| `threads.py` | Threads |
| `voice.py` | Voice |
| `telemetry.py` | Telemetry |
| `__init__.py` | Re-exports the most common schemas |
| `admin/` | Admin-only schemas (see `admin/README.md`) |

## Common types

### SnowflakeID

`SnowflakeID` is a string subclass that accepts `int` or numeric `str` and always serializes as a string (`src/api/schemas/common.py:12`).

```python
from src.api.schemas.common import SnowflakeID
from pydantic import BaseModel

class MyResponse(BaseModel):
    id: SnowflakeID  # Serializes as "123456789012345678"
```

Validation rejects non-numeric strings.

### Pagination

Use `PaginationParams` for cursor pagination:

```python
from typing import Optional
from fastapi import Query
from src.api.schemas.common import SnowflakeID

async def list_items(
    limit: int = Query(default=50, ge=1, le=100),
    before: Optional[SnowflakeID] = Query(default=None),
    after: Optional[SnowflakeID] = Query(default=None),
):
    pass
```

`PaginatedResponse[T]` wraps paginated results with `items`, `has_more`, and optional `total`.

## Channel types

`channel_type` identifies the kind of channel. The allowed values depend on the schema:

* `servers.ChannelCreateRequest.channel_type` — `text`, `voice`, or `category` (default `text`). See `src/api/schemas/servers.py:85`.
* `servers.ChannelResponse.channel_type` — `text`, `voice`, or `category` (required).
* `channels.DMChannelResponse.channel_type` — always `"dm"` (default `dm`). See `src/api/schemas/channels.py:44`.
* `channels.NotesChannelResponse.channel_type` — always `"notes"` (default `notes`). See `src/api/schemas/channels.py:68`.

When you create a server channel, set `topic`, `category_id`, `nsfw`, `slowmode_seconds`, and `read_receipts_enabled` as needed. For DMs, use `DMChannelCreateRequest` with `recipient_id`; for personal notes, the response always includes `name = "Personal Notes"`.

## Message and read receipts

`MessageResponse` (`src/api/schemas/messages.py:94`) includes several read-related fields. They are distinct:

| Field | Type | Description |
|-------|------|-------------|
| `read` | `bool` | Whether the current user has read this message |
| `read_count` | `int` | Number of users who have read the message |
| `read_by` | `List[ReaderInfo]` | List of users who read the message. Each entry has `id` (`SnowflakeID`), `username`, and optional `avatar_url` |
| `delivery_count` | `int` | Number of users who received the message |
| `status` | `Optional[str]` | Current-user status: `sent`, `delivered`, or `read` |

Use `read_by` when you need per-reader detail; use `read` and `read_count` for summary state. `ReaderInfo` is defined at `src/api/schemas/messages.py:84`.

## Messaging settings

`MessagingSettingsResponse` and `MessagingSettingsUpdateRequest` (`src/api/schemas/messages.py:153`) control per-user messaging behavior:

`read_receipts_enabled`, `typing_indicators_enabled`, `compact_messages_enabled`, `allow_dms_from` (`everyone` | `friends` | `none`), `auto_create_dms`, `max_message_length`, `max_attachment_size`, and `max_attachments_per_message`.

## Rate limits

Schemas themselves do not enforce rate limits, but the DSAR endpoints do. `POST /users/@me/data-export` allows one request per 24 hours (`limit=1, window_seconds=86400` in `src/api/routes/users/dsar.py:77`). When the limit is exceeded, the API returns `429` with:

```json
{"error": {"code": 429, "message": "Data export request limit exceeded. Please wait 24 hours between requests."}}
```

The download endpoint (`GET /users/@me/data-export/{request_id}/download`) returns `410 Download link has expired` when `expires_at` is in the past.

## Health and gateway

Health checks are not schemas. Use `GET /health` and `GET /ready` (`src/api/routes/health.py`):

* `GET /ready` — minimal liveness probe for orchestrators. Returns `{"status": "ready"}` with `200`.
* `GET /health` — detailed health response `HealthResponse` with `status`, `version`, optional `events_metrics`, `is_queue_recreate_pending`, and `cross_worker_listener`. Returns `500` only when the handler itself fails; event-manager metrics are advisory.

Do not use `/health` as a gateway. WebSocket gateway events (such as `emit_user_update`) are emitted server-side and are not part of the REST schema contract.

## Request models

Request models validate input with Pydantic `Field` constraints:

```python
from pydantic import BaseModel, Field
from typing import Optional

class CreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=1000)
```

For DSAR, the request requires password verification:

```python
from src.api.schemas.dsar import DSARRequestBody

# POST /users/@me/data-export
body = DSARRequestBody(password="current-password", format="json")  # or "zip"
```

## Response models

Response models set `model_config = ConfigDict(from_attributes=True)` so you can construct them from ORM objects:

```python
from pydantic import BaseModel, ConfigDict
from src.api.schemas.common import SnowflakeID

class ItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID
    name: str
    created_at: int
```

## Use schemas in routes

```python
from src.api.schemas.auth import RegisterRequest, LoginResponse

@router.post("/register", response_model=LoginResponse)
async def register(body: RegisterRequest):
    # body is validated automatically
    pass
```

All schemas at `src/api/schemas/` are importable directly. For admin schemas, import from `src.api.schemas.admin` (see `admin/README.md`).
