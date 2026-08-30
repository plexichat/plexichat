# Settings

Manage per-user key-value settings with validation, limits, and legacy-key filtering. `SettingsManager` is the single entry point for all user preference storage.

## Overview

Use this module to store and retrieve user preferences such as themes, UI options, and client configuration. Settings are scoped per user, persisted in the `user_settings` table, and synchronized across devices.

This module does not provide HTTP handlers. Wire `SettingsManager` into your application and call its methods from your route layer.

## Setup

Initialize the manager with a connected database handle.

```python
from src.core.settings.manager import SettingsManager
from src.core.settings.models import SettingsConfig

# Use defaults from SettingsConfig
manager = SettingsManager(db)

# Or provide custom limits
config = SettingsConfig(
    max_settings_per_user=200,
    max_key_length=64,
    max_value_length=4096,
)
manager = SettingsManager(db, config=config)
```

The module-level helpers in `src/core/settings/__init__.py` expose `setup`, `get_setting`, `set_setting`, and related functions that delegate to the shared manager instance.

## Configuration

`SettingsConfig` is defined in `models.py`. The defaults are:

| Field | Default | Description |
|---|---|---|
| `max_settings_per_user` | `100` | Maximum number of keys per user. |
| `max_key_length` | `64` | Maximum key length in characters. |
| `max_value_length` | `2048` | Maximum value length in characters. |
| `key_charset` | `a-z0-9_-` | Allowed characters in keys (case-insensitive). |
| `reserved_prefixes` | `["__", "_"]` | Prefixes rejected on user-facing writes. |
| `reserved_keys` | `["__internal"]` | Reserved internal prefixes. |
| `server_setting_keys` | `["_server_muted", ...]` | Keys writable only through `set_server_setting`. |

## Usage

### Get and set a single setting

```python
from src.core.settings import setup, get_setting, set_setting

setup(db)

# Write a setting
setting = set_setting(user_id, "theme", "dark")
print(setting.value)  # "dark"

# Read a setting (returns the value string or None)
theme = get_setting(user_id, "theme")

# Read as a UserSetting object
from src.core.settings.manager import SettingsManager
manager = SettingsManager(db)
obj = manager.get_one(user_id, "theme")
print(obj.key, obj.value, obj.updated_at)
```

### Write multiple settings atomically

`set_many` validates all keys and values up front, checks the per-user limit, and executes all inserts and updates in a single transaction. If any entry fails, the entire batch rolls back.

```python
results = manager.set_many(user_id, {
    "theme": "dark",
    "language": "en",
    "notifications": "enabled",
})
# results is Dict[str, UserSetting]
```

See `manager.py:313`.

### Server-managed settings

Keys that start with `_` are reserved for server semantics and cannot be written through `set` or `set_many`. Use `set_server_setting` for the known server key set:

```python
manager.set_server_setting(user_id, "_server_muted", "true")
```

See `manager.py:261`.

### Read all settings

```python
# As a dict (legacy/invalid keys are filtered out)
all_settings: dict[str, str] = manager.get_all(user_id)

# As a list of objects
settings_list: list[UserSetting] = manager.get_all_as_list(user_id)

# Count
count: int = manager.get_count(user_id)
```

### Delete a setting

```python
deleted: bool = manager.delete(user_id, "theme")
# Returns True if a row was deleted, False if not found
```

### Aliases

The manager provides long-name aliases for compatibility with route-layer and test code. See `manager.py:496`.

| Short name | Alias |
|---|---|
| `get` | `get_setting` |
| `set` | `set_setting` |
| `delete` | `delete_setting` |
| `get_all` | `get_all_settings` |
| `get_all_as_list` | `get_settings_list` |
| `get_count` | `get_settings_count` |

## Validation rules

The key contract is documented in `manager.py:21` and enforced by `_validate_key` at `manager.py:47`.

* Keys must be non-empty strings after trimming.
* Keys must match the charset `[a-z0-9_-]` (case-insensitive, full match).
* Keys must not exceed `max_key_length`.
* Keys must not start with a reserved prefix (`__` or `_`). Server-semantics keys are only writable through `set_server_setting` and must be in `server_setting_keys`.
* Values must not exceed `max_value_length`.

On read, legacy rows that predate the charset contract are filtered gracefully instead of raising, so old data does not break API responses. Recognized server-managed keys remain readable. See `manager.py:103`.

## Error handling

All exceptions are defined in `exceptions.py` and inherit from `SettingsError`.

| Exception | When it is raised |
|---|---|
| `SettingsLimitExceeded` | User has reached `max_settings_per_user`, or a bulk write would exceed it. |
| `SettingsKeyTooLong` | Key exceeds `max_key_length`. |
| `SettingsValueTooLong` | Value exceeds `max_value_length`. |
| `SettingsKeyReserved` | Key uses a reserved prefix or is not a recognized server-managed key. |
| `SettingsKeyInvalid` | Key is empty, not a string, or contains characters outside the charset. |

Example:

```python
from src.core.settings.exceptions import (
    SettingsLimitExceeded,
    SettingsKeyInvalid,
    SettingsValueTooLong,
)

try:
    manager.set(user_id, "theme", "dark")
except SettingsLimitExceeded:
    print("Too many settings")
except SettingsKeyInvalid as e:
    print(f"Invalid key: {e}")
except SettingsValueTooLong as e:
    print(f"Value too long: {e}")
```

## Files

| File | Description |
|---|---|
| `manager.py` | `SettingsManager` class that implements all CRUD operations. |
| `models.py` | `UserSetting` and `SettingsConfig` models. |
| `schema.py` | Pydantic schemas for request and response validation. |
| `exceptions.py` | `SettingsError` and its subclasses. |
| `__init__.py` | Module-level `setup` and delegating helpers. |

## Related code

* `src/core/base.py` — `BaseManager`, `SnowflakeID`, `timestamp_ms`, `generate_id` used by `SettingsManager`.
* `src/core/settings/schema.py` — Request validation that mirrors the manager's key and value constraints.
