# Profiles

Manage user profiles in Plexichat, including bio, custom status, social links, and personal details.

## Overview

The profiles module provides the `ProfileManager` class for creating, reading, and updating user profiles. The manager validates fields, normalizes storage, and exposes a consistent API for read and write operations.

Profiles are stored in the `user_profiles` table. A profile row is created lazily on the first write; reads for users without a row return an in-memory default without writing to the database.

## Before you begin

Ensure you have a connected database instance and, optionally, an initialized auth module for presence synchronization.

## Files

| File | Description |
|------|-------------|
| `__init__.py` | Module exports. Exports `ProfileManager`. |
| `manager.py` | Business logic for profile CRUD, validation, and custom status expiration. |

## Profile fields

| Field | Constraint | Notes |
|-------|------------|-------|
| `bio` | Up to 1000 characters | Empty string clears the field (stored as `NULL`). |
| `custom_status_text` | Up to 128 characters | Optional expiration timestamp. |
| `custom_status_emoji` | Up to 32 characters | Emoji or short string for status. |
| `custom_status_expires_at` | Epoch milliseconds or seconds | Must be in the future. `None` clears expiration. |
| `social_links` | Up to 10 links | Each entry is `{"platform": "...", "url": "..."}`. Allowed platforms: `github`, `twitter`, `linkedin`, `website`, `youtube`, `twitch`, `discord`, `reddit`, `mastodon`, `other`. URLs without a scheme get `https://` prepended. `javascript:` and `data:` schemes are rejected. |
| `pronouns` | Up to 40 characters | Empty string clears the field. |
| `location` | Up to 100 characters | Empty string clears the field. |
| `timezone` | Up to 64 characters | Empty string clears the field. |
| `banner_url` | URL string | Must use `http` or `https`. `javascript:`, `data:`, and `vbscript:` schemes are rejected. |

## Use `ProfileManager`

### Create a manager

```python
from src.core.profiles import ProfileManager

mgr = ProfileManager(db, auth_module)
```

### Get a profile

`get_profile` is read-only and never mutates the database. If no row exists, it returns a default in-memory profile.

```python
profile = mgr.get_profile(user_id)
```

The returned dict is stripped of internal bookkeeping keys and includes `social_links` as a parsed list.

### Update a profile

Pass only the fields you want to change. `None` leaves a field unchanged; an empty string or `[]` (for `social_links`) clears it. The manager issues a single `UPDATE` statement per call.

```python
profile = mgr.update_profile(
    user_id=123,
    bio="Hello, I'm a developer!",
    pronouns="they/them",
    location="Berlin",
    social_links=[{"platform": "github", "url": "https://github.com/user"}],
    banner_url="https://example.com/banner.png",
)
```

### Manage custom status

Use a sentinel to support partial updates. Omit a parameter to leave it unchanged; pass `None` to clear it.

```python
# Set status with expiration
profile = mgr.set_custom_status(
    user_id=123,
    text="Working",
    emoji="💻",
    expires_at=1730000000000,  # milliseconds or seconds; auto-detected
)

# Clear only the emoji, keep text and expiration
profile = mgr.set_custom_status(user_id=123, emoji=None)

# Clear the entire status
profile = mgr.clear_custom_status(user_id=123)
```

Expired statuses are flagged on read and cleared on the next write or by a background reaper:

```python
count = mgr.reap_expired_custom_statuses()
```

### Get multiple profiles

```python
profiles = mgr.get_bulk_profiles([123, 456, 789])
# Returns {user_id: profile_dict} with in-memory defaults for missing users
```

## Custom status behavior

* Reads do not execute `UPDATE` statements. Expiration is flagged with an internal `_custom_status_expired` key that is stripped before the response is returned.
* Writes are dual-committed to `user_profiles` and `auth_users` inside a transaction so presence and profile data stay consistent.
* Expiration timestamps accept both seconds and milliseconds. Values below `10_000_000_000` are treated as seconds and converted.

## Error handling

| Condition | Error |
|-----------|-------|
| `bio`, `pronouns`, `location`, or `timezone` exceeds max length | Value is truncated, not rejected. |
| `banner_url` uses an unsupported scheme | `ValueError` |
| `social_links` exceeds 10 entries | `ValueError` |
| `expires_at` is not in the future | `ValueError` |
