# Messages CRUD sub-package

## Overview

This package implements the core message create, read, update, and delete endpoints. You compose the router from focused mixins so each file owns a single responsibility and `composer.py` registers all routes in one place.

## Architecture

```text
messages_crud/
├── __init__.py   # Module-level APIRouter + route registration
├── base.py       # MessagesCRUDBase — shared helpers and fallback logic
├── send.py       # SendMixin — create message, reply, and poll creation
├── retrieve.py   # RetrieveMixin — get message by ID
├── edit.py       # EditMixin — edit message content
├── delete.py     # DeleteMixin — delete message, bulk-delete, and poll cleanup
├── broadcast.py  # BroadcastMixin — WebSocket event dispatch
├── composer.py   # MessagesCRUDRouter — combines all mixins and calls register_routes()
└── README.md     # This file
```

## Inheritance order

`MessagesCRUDRouter` uses C3 linearization (MRO):

```text
MessagesCRUDRouter
  → SendMixin
  → RetrieveMixin
  → EditMixin
  → DeleteMixin
  → BroadcastMixin
  → MessagesCRUDBase
```

Concrete mixins come first. `BroadcastMixin` (fire-and-forget WebSocket dispatch) sits before `MessagesCRUDBase`, which provides shared helpers.

## Routes registered in `composer.py:72`

| Method | Path | Handler | Summary |
|--------|------|---------|---------|
| `POST` | `/channels/{channel_id}/messages` | `SendMixin.send_channel_message()` | Send message |
| `GET` | `/channels/{channel_id}/messages/{message_id}` | `RetrieveMixin.get_message()` | Get message |
| `PATCH` | `/channels/{channel_id}/messages/{message_id:int}` | `EditMixin.edit_message()` | Edit message |
| `DELETE` | `/channels/{channel_id}/messages/{message_id}` | `DeleteMixin.delete_message()` | Delete message |
| `POST` | `/channels/{channel_id}/messages/bulk-delete` | `DeleteMixin.bulk_delete_messages()` | Bulk-delete messages |

`__init__.py:13` creates `router = APIRouter(tags=["Messages"])` and calls `MessagesCRUDRouter().register_routes(router)` immediately.

## Key design decisions

1. **Function to method** — All module-level functions become instance methods so they share helpers through `self._send_message_with_fallback()`, `self._resolve_author_info()`, `self._resolve_channel_scope()`, and `self._broadcast_*()`.

2. **Decorator to `register_routes()`** — The `@router.post(...)` decorators become explicit `router.post(path)(method)` calls in `composer.py:72`. All OpenAPI metadata (`response_model`, `summary`, `responses`) is preserved in the composer.

3. **Broadcast extraction** — Inline WebSocket dispatch is extracted into `BroadcastMixin._broadcast_message_create()`, `BroadcastMixin._broadcast_message_update()`, and `BroadcastMixin._broadcast_message_delete()` (`broadcast.py:93`). They resolve recipients through `_resolve_recipients()`, which filters server members by `channels.view` permission.

4. **Author info resolution** — The pattern of resolving `author_username`, `author_avatar_url`, and `author_badges` from `TokenInfo` with a fallback to `auth.get_user()` is extracted into `MessagesCRUDBase._resolve_author_info()` (`base.py:475`).

5. **Channel scope validation** — `RetrieveMixin`, `EditMixin`, and `DeleteMixin` call `self._resolve_channel_scope()` and `self._ensure_message_in_channel()` so a URL/channel mismatch returns the same `404` as a nonexistent message and does not reveal foreign conversations (`base.py:42`).

6. **Response shape** — `_message_to_response()` in `helpers.py:40` maps `read_by_data` to `List[ReaderInfo]` (`id`, `username`, `avatar_url`). Do not treat `read_by` as a list of bare IDs.

## Request and response contracts

* `POST /channels/{channel_id}/messages` accepts `MessageCreateRequest` (`content`, `reply_to_id`, `attachments`, `embeds`, `poll`). At least one of `content`, `attachments`, `embeds`, or `poll` is required. Returns `MessageResponse`.
* `POST /channels/{channel_id}/messages/bulk-delete` accepts `BulkDeleteRequest` (`message_ids: List[SnowflakeID]`, `max_length=100`). The handler returns `400` when you send more than 100 IDs and `SuccessResponse(success=False)` when no eligible messages are deleted (`delete.py:218`).
* `PATCH /channels/{channel_id}/messages/{message_id:int}` accepts `MessageUpdateRequest` (`content`, `expected_edited_at`). When `expected_edited_at` is stale you receive `409 EDIT_CONFLICT`; when the edit window has expired you receive `403 EDIT_WINDOW_EXPIRED`.

## How you add a new route

1. Add the handler method to the appropriate mixin, or create a new mixin file if the route belongs to a new category.
2. In `composer.py`, add `router.<method>("/path", ...)(self.handler)` to `register_routes()`.
3. If you added a new mixin, include it in the `MessagesCRUDRouter` class definition and place it before `BroadcastMixin` in the MRO.

Example:

```python
# In my_feature.py
class MyFeatureMixin(MessagesCRUDBase):
    async def my_handler(self, channel_id: str, current_user: TokenInfo = Depends(get_current_user)):
        ...

# In composer.py
class MessagesCRUDRouter(MyFeatureMixin, SendMixin, ..., MessagesCRUDBase):
    def register_routes(self, router: APIRouter) -> None:
        router.get("/channels/{channel_id}/my-feature", ...)(self.my_handler)
        ...
```

## Migration note

This refactoring preserves the exact same route paths, handler signatures, error handling, and response schemas as the original monolithic `messages_crud.py`. No functional changes were introduced beyond extracting the mixins.
