# Presence Manager

## Purpose
Tracks user presence, activities, typing indicators, and custom status
with server-aware visibility rules.

## Primary Responsibilities
- Maintain presence and status records (online, idle, dnd, offline, invisible)
- Track typing indicators and auto-expire them
- Store and clear custom statuses with expiry
- Update Redis presence caches when available
- Enforce visibility rules (invisible mode, block relationships)
- Support batch presence queries for friend lists and server member lists

## Architecture (Mixin-based)
The `PresenceManager` class is composed via mixins:

| File | Mixin | Responsibility |
|------|-------|---------------|
| `composer.py` | `PresenceManager` | Composition orchestrator |
| `base.py` | `PresenceManagerBase` | Typed `self._db`, `self._relationships`, `self._servers`, `self._config` + `__init__`, `_load_config`, `_validate_user`, `_ensure_presence_record`, `_presence_to_dict`, `_dict_to_presence` |
| `status.py` | `StatusMixin` | `set_status`, `get_status`, `clear_status`, `_update_redis_presence` |
| `custom_status.py` | `CustomStatusMixin` | `set_custom_status`, `get_custom_status`, `clear_custom_status`, `_cleanup_expired_custom_status` |
| `activity.py` | `ActivityMixin` | `set_activity`, `get_activity`, `clear_activity` |
| `typing.py` | `TypingMixin` | `start_typing`, `stop_typing`, `get_typing_users`, `get_user_typing_channels`, `clear_all_typing`, `_cleanup_expired_typing` |
| `presence.py` | `PresenceMixin` | `get_presence`, `get_presences`, `update_last_seen`, `set_focused_channel`, `_cleanup_expired_custom_status_batch` |
| `queries.py` | `OnlineQueryMixin` | `get_online_friends`, `get_online_server_members` |
| `visibility.py` | `VisibilityMixin` | `get_visible_presence`, `get_visible_presences_bulk`, `can_see_presence` |

### Pyright Compatibility
`PresenceManagerBase` declares `_db`, `_auth`, `_relationships`, `_servers`,
`_config`, `_typing_timeout_ms`, and `_presence_timeout_ms` as class-level typed
attributes. Every mixin inherits from this base class, so pyright sees all `self.*`
references as known, typed attributes. No `# type: ignore` comments or file-level
suppressions are needed.

## Composition

```python
class PresenceManager(
    StatusMixin,
    CustomStatusMixin,
    ActivityMixin,
    TypingMixin,
    PresenceMixin,
    OnlineQueryMixin,
    VisibilityMixin,
    PresenceManagerBase,
):
    """Core presence manager handling all presence operations."""
```

The MRO is: `PresenceManager -> StatusMixin -> CustomStatusMixin -> ActivityMixin ->
TypingMixin -> PresenceMixin -> OnlineQueryMixin -> VisibilityMixin ->
PresenceManagerBase -> BaseManager`. All shared state lives in
`PresenceManagerBase.__init__`.

## Dependencies
- `BaseManager` — provides `self._db`, `self._auth`, `_get_timestamp()`, `_generate_id()`, `_user_exists()`
- Auth module for user validation
- Relationships module for visibility filtering (block checks, friend queries)
- Servers module for member presence visibility (`get_online_server_members`)
- Redis cache helpers for high-speed lookups (`cache_set`, `get_cached_presence`, `redis_available`)
- Database indexes on `pres_presence`, `pres_activity`, `pres_custom_status`, and `pres_typing` tables

## Usage

```python
from src.core.presence import PresenceManager

pm = PresenceManager(db, auth_module=auth, relationships_module=relationships,
                     servers_module=servers)

# Set user status
presence = pm.set_status(user_id=1, status="online")

# Set a custom status
presence = pm.set_custom_status(user_id=1, text="In a meeting", emoji="📋",
                                expires_at=1700000000000)

# Set activity (e.g., a game)
presence = pm.set_activity(user_id=1, activity_type="playing", name="Chess",
                           details="Ranked match", state="3-2")

# Start typing indicator
indicator = pm.start_typing(user_id=1, channel_id=10)

# Get full presence for a user
presence = pm.get_presence(user_id=1)
print(presence.status, presence.activity, presence.custom_status)

# Get presence as visible to another user (respects blocks + invisible)
visible = pm.get_visible_presence(viewer_id=2, target_id=1)

# Batch presence query for friends
online_friends = pm.get_online_friends(user_id=1)

# Set focused channel (Redis-only, suppresses notifications)
pm.set_focused_channel(user_id=1, channel_id=10, server_id=1)
```

Or use the convenience module API after setup:

```python
from src.core import presence

presence.setup(db, auth_module=auth)
presence.set_status(user_id=1, status="online")
```

## Error Handling

Presence operations raise exceptions from `src.core.presence.exceptions`:

- `UserNotFoundError` — Target user does not exist (raised by `_validate_user`).
- `InvalidStatusError` — Status string is not a valid `UserStatus` value.
- `InvalidActivityError` — Activity name is empty or invalid.

```python
from src.core.presence.exceptions import InvalidStatusError, InvalidActivityError

try:
    pm.set_status(user_id=1, status="invalid_status")
except InvalidStatusError as e:
    print(f"Invalid status: {e}")

try:
    pm.set_activity(user_id=1, activity_type="playing", name="")
except InvalidActivityError as e:
    print(f"Activity name is required: {e}")
```

Redis failures are handled gracefully — all Redis operations catch exceptions at
the debug log level and fall back to database queries. A missing Redis backend
does not break presence functionality.

## Notes
- All mixin methods only access state via `self` — no cross-mixin coupling.
- The `set_focused_channel` method stores state **only** in Redis (transient).
- Typing indicators use Redis SETs for near-instant read/write, with the
  database as a fallback.
- Online user set is maintained in Redis (`presence:online_users`) for O(1)
  set intersection queries.
- Bulk presence queries first attempt Redis cache via `get_bulk_presence`,
  then fall back to batched SQL queries with JOINs.
- Expired typing indicators are cleaned from the database on each write.
- Expired custom statuses are cleaned on read when queried individually,
  and on batch cleanup before bulk queries.
