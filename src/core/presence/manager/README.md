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

## File Layout

The presence manager is defined entirely in `__init__.py` as a single `PresenceManager` class
(small enough that it did not require mixin decomposition). It provides:

| Category | Methods |
|----------|---------|
| Status | `set_status`, `get_status`, `clear_status` |
| Custom Status | `set_custom_status`, `get_custom_status`, `clear_custom_status` |
| Activity | `set_activity`, `get_activity`, `clear_activity` |
| Presence | `get_presence`, `get_presences`, `update_last_seen` |
| Focus | `set_focused_channel` |
| Typing | `start_typing`, `stop_typing`, `get_typing_users`, `get_user_typing_channels`, `clear_all_typing` |
| Online Queries | `get_online_friends`, `get_online_server_members` |
| Visibility | `get_visible_presence`, `get_visible_presences_bulk`, `can_see_presence` |

## Core Components
- PresenceManager: presence orchestration and state updates
- Presence models: status (UserStatus), activity (ActivityType), typing, and custom status
- Redis cache helpers for high-speed lookups

## Usage

```python
from src.core.presence import PresenceManager

pm = PresenceManager(db, auth_module=auth, relationships_module=relationships,
                     servers_module=servers)

# Set user status
presence = pm.set_status(user_id=1, status="online")

# Set a custom status
presence = pm.set_custom_status(user_id=1, text="In a meeting", emoji="📋", expires_at=1700000000000)

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

Redis failures are handled gracefully — all Redis operations catch exceptions at the debug log level and fall back to database queries. A missing Redis backend does not break presence functionality.

## Dependencies
- Auth module for user validation (`_user_exists`).
- Relationships module for visibility filtering (block checks, friend queries).
- Servers module for member presence visibility (`get_online_server_members`).
- Redis cache helpers for high-speed lookups (`cache_set`, `cache_get`, `redis_available`).
- Database indexes on `pres_presence`, `pres_activity`, `pres_custom_status`, and `pres_typing` tables.

## Data and Caching
- Presence states are cached in Redis for fast retrieval (TTL: 300s default).
- Typing indicators use Redis SETs for near-instant read/write, with the database as a fallback.
- Online user set is maintained in Redis (`presence:online_users`) for O(1) set intersection queries.
- Focus state is stored **only** in Redis — it is transient and does not need persistence.
- Bulk presence queries first attempt Redis cache, then fall back to batched SQL queries with JOINs.
- Typing indicators are periodically cleaned from the database (expired entries deleted on each write operation).
- Custom statuses are auto-cleaned on read when expired.
