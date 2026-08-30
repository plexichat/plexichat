# Presence manager

Track user presence, activities, typing indicators, and custom status with server-aware visibility rules.

## Overview

The `PresenceManager` class owns the persistence, caching, and visibility logic for presence. It maintains status records, typing indicators, custom statuses with expiry, and Redis-backed caches for fast lookups.

Use `PresenceManager` directly, or use the convenience functions in `src.core.presence` after you call `presence.setup()`.

## Architecture

`PresenceManager` is composed from mixins. `PresenceManagerBase` provides typed shared state so `pyright` resolves `self` attributes without suppression comments.

| File | Mixin | Responsibility |
|------|-------|---------------|
| `composer.py` | `PresenceManager` | Composition orchestrator. |
| `base.py` | `PresenceManagerBase` | Typed `self._db`, `self._relationships`, `self._servers`, `self._config`, init, `_load_config`, `_validate_user`, `_ensure_presence_record`, `_presence_to_dict`, `_dict_to_presence`. |
| `status.py` | `StatusMixin` | `set_status`, `get_status`, `clear_status`, `_update_valkey_presence`, `sweep_all_offline`. |
| `custom_status.py` | `CustomStatusMixin` | `set_custom_status`, `get_custom_status`, `clear_custom_status`, `_cleanup_expired_custom_status`. |
| `activity.py` | `ActivityMixin` | `set_activity`, `get_activity`, `clear_activity`. |
| `typing.py` | `TypingMixin` | `start_typing`, `stop_typing`, `get_typing_users`, `get_user_typing_channels`, `clear_all_typing`, `_cleanup_expired_typing`. |
| `presence.py` | `PresenceMixin` | `get_presence`, `get_presences`, `update_last_seen`, `set_focused_channel`, `_cleanup_expired_custom_status_batch`. |
| `queries.py` | `OnlineQueryMixin` | `get_online_friends`, `get_online_server_members`. |
| `visibility.py` | `VisibilityMixin` | `get_visible_presence`, `get_visible_presences_bulk`, `can_see_presence`. |

### Composition

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

Method resolution order: `PresenceManager` → `StatusMixin` → `CustomStatusMixin` → `ActivityMixin` → `TypingMixin` → `PresenceMixin` → `OnlineQueryMixin` → `VisibilityMixin` → `PresenceManagerBase` → `BaseManager`.

Shared state lives in `PresenceManagerBase.__init__`.

## Dependencies

- `BaseManager` — Provides `self._db`, `self._auth`, `_get_timestamp()`, `_generate_id()`, and `_user_exists()`.
- Auth module for user validation.
- Relationships module for block checks and friend queries.
- Servers module for server-member visibility (`get_online_server_members`).
- Redis/Valkey helpers (`cache_set`, `get_cached_presence`, `valkey_available`) for high-speed lookups.
- Database indexes on `pres_presence`, `pres_activity`, `pres_custom_status`, and `pres_typing`.

## Configuration

The manager loads `presence` from `config.yaml`.

| Key | Default | Description |
|-----|---------|-------------|
| `typing_timeout_ms` | `6000` | How long a typing indicator remains valid. |
| `timeout_ms` | `300000` | How long before a user is considered offline. |

## Usage

### Initialize the manager

```python
from src.core.presence import PresenceManager

pm = PresenceManager(db, auth_module=auth, relationships_module=relationships,
                     servers_module=servers)

# Set status
presence = pm.set_status(user_id=1, status="online")

# Set a custom status
presence = pm.set_custom_status(user_id=1, text="In a meeting", emoji="📋",
                                expires_at=1700000000000)

# Set an activity
presence = pm.set_activity(user_id=1, activity_type="playing", name="Chess",
                           details="Ranked match", state="3-2")

# Start a typing indicator
indicator = pm.start_typing(user_id=1, channel_id=10)

# Get full presence
presence = pm.get_presence(user_id=1)
print(presence.status, presence.activity, presence.custom_status)

# Get presence as visible to another user (respects blocks and invisible mode)
visible = pm.get_visible_presence(viewer_id=2, target_id=1)

# Batch query for online friends
online_friends = pm.get_online_friends(user_id=1)

# Set focused channel (stored in Redis only; suppresses notifications)
pm.set_focused_channel(user_id=1, channel_id=10, server_id=1)
```

### Use the module API

After setup, you can call the module-level helpers:

```python
from src.core import presence

presence.setup(db, auth_module=auth, relationships_module=relationships,
               servers_module=servers)
presence.set_status(user_id=1, status="online")
```

## Behavior

### Caching

- Mutating methods write through to Redis (`presence:{user_id}`) with a TTL derived from `timeout_ms`.
- The online-users set (`presence:online_users`) is maintained in Redis for O(1) set intersection queries. It has a bounded TTL and is revalidated periodically against the database to evict stale members.
- Bulk reads try `get_bulk_presence` from Redis first, then fall back to batched SQL JOINs.

### Expiry and cleanup

- Expired typing indicators are removed on each write to `pres_typing`.
- Expired custom statuses are removed on read for single-user queries, and via `_cleanup_expired_custom_status_batch` before bulk queries.

### Visibility

- A viewer always sees their own real presence.
- Blocked users see the target as `offline` with no custom status, activity, or timestamps.
- A user with `invisible` status appears as `offline` to everyone else. The manager scrubs `custom_status`, `last_seen`, and `updated_at` so the response is indistinguishable from a user who has never been online.

### Focused channel

`set_focused_channel` writes only to Redis. It is transient and does not persist in the database.

## Error handling

Presence operations raise exceptions from `src.core.presence.exceptions`:

| Exception | When it is raised |
|-----------|-------------------|
| `UserNotFoundError` | Target user does not exist (`_validate_user`). |
| `InvalidStatusError` | Status string is not a valid `UserStatus` value. |
| `InvalidActivityError` | Activity name is empty or invalid. |

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

Redis failures are handled gracefully. All Redis operations catch exceptions, log at debug level, and fall back to database queries. Presence remains functional without Redis.

## Notes

- Mixin methods access shared state only through `self`. There is no cross-mixin coupling beyond `PresenceManagerBase`.
- `sweep_all_offline` runs under a distributed lock so only one worker sweeps on startup in multi-worker deployments.
