# Relationships router sub-package

## Overview

This package exposes friend, block, and relationship-listing endpoints behind `GET /relationships/@me` and related routes. You interact with the router through a single `router` exported from `__init__.py`.

This package is a class-based mixin refactoring of the former monolithic `__init__.py`. Each file owns one responsibility and `composer.py` combines them.

## Architecture

```text
relationships/
├── __init__.py          # Module-level APIRouter + route registration
├── base.py              # RelationshipsBase — event dispatch and cache invalidation
├── listing.py           # ListingMixin — GET /@me (list all relationships)
├── friend_requests.py   # FriendRequestMixin — POST "" (create), PUT /{user_id}/accept
├── blocking.py          # BlockingMixin — POST /block
├── deletion.py          # DeletionMixin — DELETE /{user_id}
├── composer.py          # RelationshipsRouter — combines all mixins and calls register_routes()
├── helpers.py           # Schema helpers (pre-existing, unchanged)
└── README.md            # This file
```

## Inheritance order

`RelationshipsRouter` uses C3 linearization (MRO):

```text
RelationshipsRouter
  → ListingMixin
  → FriendRequestMixin
  → BlockingMixin
  → DeletionMixin
  → RelationshipsBase
```

## Routes registered in `composer.py:32`

| Method | Path | Handler | Description |
|--------|------|---------|-------------|
| `GET` | `/@me` | `ListingMixin.get_relationships()` | List all relationships for the current user |
| `POST` | `` | `FriendRequestMixin.create_relationship()` | Create a friend request |
| `PUT` | `/{user_id}/accept` | `FriendRequestMixin.accept_friend_request()` | Accept a pending friend request |
| `POST` | `/block` | `BlockingMixin.block_user()` | Block a user |
| `DELETE` | `/{user_id}` | `DeletionMixin.delete_relationship()` | Remove a friend, cancel a request, or unblock |

The prefix `/relationships` is applied by the parent router mount, so the full paths are `/relationships/@me`, `/relationships`, and so on. `__init__.py:12` creates `router = APIRouter(tags=["Relationships"])` and calls `RelationshipsRouter().register_routes(router)` once at import time.

## Query parameters and caching for `GET /@me`

You call `GET /@me` with optional pagination:

* `limit` — `1–500` (default `100`)
* `offset` — `>= 0` (default `0`)

The handler is cached for 60 seconds (`@cached(ttl=60, prefix="relationships_api")` in `listing.py:20`). It bulk-fetches user profiles through `auth.get_user_profiles_bulk()` and visible presences through `presence.get_visible_presences_bulk()` so a single request does not produce N+1 lookups.

The response is `List[DetailedRelationshipInfo]` where each item contains:

* `user_id` (`str`), `username`, `avatar_url`
* `status` — `friend`, `pending_incoming`, `pending_outgoing`, or `blocked`
* `presence` — `PresenceInfo(status)` (`online`, `idle`, `dnd`, `offline`)
* `message` and `created_at` — present for pending requests

`DELETE /{user_id}` takes `user_id: SnowflakeIDParam`, matching the other snowflake-id routes. Malformed IDs fail Pydantic validation with `422`.

## Key design decisions

1. **Function to method** — All module-level functions become instance methods so they share state through `self._dispatch_relationship_event()` and `self._invalidate_relationship_list_cache()` in `RelationshipsBase`.

2. **Decorator to `register_routes()`** — The `@router.get(...)` decorators become explicit `router.get(path, ...)(method)` calls in `composer.py:32`. All OpenAPI metadata (`response_model`, `summary`, `responses`) is preserved in the composer.

3. **Module-level router** — `__init__.py` instantiates `RelationshipsRouter` once and exposes only `router`. This matches the original public API.

4. **Cache invalidation** — `RelationshipsBase` invalidates `prefix="relationships_api"` after mutations so `GET /@me` does not serve stale data.

## Error handling

| HTTP code | When you see it |
|-----------|-----------------|
| `400` | Invalid `user_id` or self-request |
| `401` | Invalid or expired token |
| `403` | Target user has blocked you (`create_relationship`) |
| `404` | User or relationship not found; friend request not found on `PUT /{user_id}/accept` |
| `409` | Relationship already exists or user is already blocked |
| `500` | Relationships module unavailable or unexpected failure |

## Dependencies

* `src.api.get_relationships()` — persistence and business rules.
* `src.api.get_auth()` — `get_user_profiles_bulk()` for username and avatar enrichment.
* `src.api.get_presence()` — `get_visible_presences_bulk()` for `PresenceInfo`.

## How you add a new route

1. Add the handler method to the appropriate mixin, or create a new mixin file if the route belongs to a new category.
2. In `composer.py`, add `router.<method>("/path", ...)(self.handler)` to `register_routes()`.
3. If you added a new mixin, include it in the `RelationshipsRouter` class definition before `RelationshipsBase`.

## Migration note

This refactoring preserves the exact same route paths, handler signatures, caching behavior, and response schemas. No functional changes were introduced.
