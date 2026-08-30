# Soundboard

The soundboard module provides server-scoped audio clips with upload validation, role-based playback permissions, per-user cooldowns, and usage tracking. It does not stream audio itself; it validates and triggers playback events that the voice module handles.

## Overview

Use this module when you need to let server members upload short sounds and play them in voice channels. The manager validates file format, size, duration, and permissions before persisting or playing a sound.

## Setup

Initialize the manager with a connected database and the servers module for permission checks.

```python
from src.core.database import Database
from src.core import auth
from src.core import servers
from src.core import soundboard

db = Database()
db.connect()

auth.setup(db)
servers.setup(db, auth, messaging)

# Recommended: wire the servers module so permission checks fail closed
soundboard.setup(db, servers)
```

Without the servers module, permission and membership checks fail closed. See `manager.py:91`.

Optional media module: pass `media_module` to `SoundboardManager` if you want to upload raw bytes through `create_sound_from_upload` instead of providing a URL directly.

## Configuration

Settings are read from the global config under `soundboard`. Defaults are defined in `manager.py:68` and `config/config.yaml`:

| Key | Default | Description |
|---|---|---|
| `max_sounds_per_server` | `100` | Maximum sounds per server. |
| `max_sound_size` | `524288` (512 KB) | Maximum file size in bytes. |
| `max_sound_duration_seconds` | `5` | Maximum duration in seconds. |
| `max_sound_name_length` | `30` | Maximum sound name length. |
| `allowed_formats` | `["mp3", "ogg"]` | Allowed audio containers. |
| `default_cooldown_seconds` | `5` | Per-user cooldown when no per-sound cooldown is set. |
| `max_cooldown_seconds` | `300` | Upper bound for per-sound cooldown. |

Volume is clamped to `0.0..1.0` on write. See `manager.py:400`.

## Usage

### Upload a sound from a URL

```python
from src.core import soundboard
from src.core.soundboard.models import SoundFormat

sound = soundboard.upload_sound(
    user_id=user_id,
    server_id=server_id,
    name="airhorn",
    format=SoundFormat.MP3,
    url="https://cdn.example.com/sounds/airhorn.mp3",
    size=245760,
    duration_seconds=2.5,
    emoji="📢",
    volume=0.8,
)
```

Requirements:

* You must have `server.soundboard.manage` or `server.manage` on the server. See `manager.py:91`.
* `name` must match `^[a-zA-Z0-9_\-]+$` and not exceed `max_sound_name_length`. See `manager.py:71`.
* `format` must be in `allowed_formats` after normalization. Both `audio/mpeg` and `mp3` are accepted. See `manager.py:163`.
* `url` must be an `http://`, `https://`, or server-relative `/...` path. Schemes such as `javascript:` and `data:` are rejected. See `manager.py:180`.
* `size` must not exceed `max_sound_size`; `duration_seconds` must not exceed `max_sound_duration_seconds`.

### Upload a sound from raw bytes

Use this path when you have file bytes and want the real container format derived from magic bytes rather than a client-supplied extension.

```python
sound = soundboard.create_sound_from_upload(
    user_id=user_id,
    server_id=server_id,
    name="airhorn",
    file_data=file_bytes,
    filename="airhorn.mp3",
    content_type="audio/mpeg",
    duration_seconds=2.5,
    emoji="📢",
    volume=0.8,
)
```

The manager detects the format from magic bytes (`ID3`, MP3 frame sync, `OggS`, `RIFF/WAVE`, WebM EBML) and rejects the upload if the detected format is not in the allow-list. If validation fails after the file is written to the media pipeline, the orphaned file is deleted. See `manager.py:235` and `manager.py:306`.

### Get and delete sounds

```python
# Get a single sound (returns None if not found or not a member)
sound = soundboard.get_sound(sound_id, user_id)

# List all sounds for a server (requires membership; ordered by name)
sounds = soundboard.get_server_sounds(user_id, server_id)
for s in sounds:
    print(f"{s.name}: {s.duration_seconds}s, used {s.usage_count} times")

# Delete a sound (requires server.soundboard.manage or server.manage)
soundboard.delete_sound(user_id, sound_id)

# Update mutable fields (name, emoji, volume, cooldown, url)
sound = soundboard.update_sound(
    user_id, sound_id, name="airhorn_v2", volume=0.6, cooldown=10
)
```

See `manager.py:451`, `manager.py:487`, and `manager.py:612`.

### Manage role-based playback permissions

By default, all members can use all sounds. When any permission rows exist, they act as an allow-list with deny-wins semantics: a user needs at least one matching `can_use=True` entry for their roles and no matching `can_use=False` entry. See `manager.py:579`.

```python
# Allow a role to use a sound
soundboard.set_sound_permissions(
    user_id=admin_id, sound_id=sound_id, role_id=member_role_id, can_use=True
)

# Deny a role from using a sound
soundboard.set_sound_permissions(
    user_id=admin_id, sound_id=sound_id, role_id=muted_role_id, can_use=False
)
```

The role must belong to the same server as the sound; cross-server roles are rejected. See `manager.py:549`.

### Play a sound

```python
# Play a sound in a voice channel
playback = soundboard.play_sound(
    user_id=user_id,
    sound_id=sound_id,
    channel_id=voice_channel_id,
)
# Returns a SoundPlayback event; the voice module streams the audio
```

`play_sound` validates membership, role-based permissions, and per-user cooldown before creating a `SoundPlayback` event and recording usage. Cooldown state is persisted in the `soundboard_user_cooldowns` table so it survives process restarts and multi-instance deployments. See `manager.py:59` and `src/core/soundboard/_playback.py`.

## Data model

### Sound (`models.py:18`)

| Field | Type | Description |
|---|---|---|
| `id` | `SnowflakeID` | Unique sound ID. |
| `server_id` | `SnowflakeID` | Owning server. |
| `name` | `str` |Validated name. |
| `format` | `SoundFormat` | `MP3` or `OGG`. |
| `emoji` | `str \| None` | Optional emoji label. |
| `url` | `str` | Validated sound URL. |
| `size` | `int` | File size in bytes. |
| `duration_seconds` | `float` | Duration. |
| `volume` | `float` | `0.0..1.0`. |
| `cooldown` / `cooldown_seconds` | `int` | Per-sound cooldown (mirrored fields, kept in sync by `__post_init__`). |
| `created_by` | `SnowflakeID` | Uploader. |
| `created_at` | `int` | Timestamp in ms. |
| `usage_count` | `int` | Aggregated from `soundboard_usage`. |

`cooldown` and `cooldown_seconds` are both concrete dataclass fields so `dataclasses.asdict` round-trips correctly. See `models.py:40`.

### Other models

| Model | File | Description |
|---|---|---|
| `SoundPermissions` | `models.py:66` | Per-role `can_use` flag. |
| `SoundCooldown` | `models.py:76` | Per-user cooldown record. |
| `SoundUsage` | `models.py:86` | Usage event row. |
| `SoundPlayback` | `models.py:98` | Event returned by `play_sound`. |

## Error handling

All exceptions inherit from `SoundboardError`. See `exceptions.py:8`.

| Exception | When it is raised |
|---|---|
| `SoundNotFoundError` | Sound does not exist or caller cannot access it. |
| `SoundLimitError` | Server has reached `max_sounds_per_server`. Includes `max_allowed` and `current`. |
| `InvalidSoundFormatError` | Format is not in `allowed_formats`. Includes `format` and `allowed`. |
| `InvalidSoundUrlError` | URL uses a disallowed scheme or is empty. |
| `SoundTooLargeError` | File exceeds `max_sound_size`. Includes `max_size` and `actual_size`. |
| `SoundTooLongError` | Duration exceeds `max_sound_duration_seconds`. Includes `max_duration` and `actual_duration`. |
| `InvalidSoundNameError` | Name is empty, too long, or contains invalid characters. |
| `SoundCooldownError` | Sound is on cooldown. Includes `remaining_seconds`. |
| `PermissionDeniedError` | Missing `server.soundboard.manage` or `server.manage`. Includes `permission`. |
| `ServerNotFoundError` | Target server does not exist. |
| `ChannelNotFoundError` | Channel does not exist or is not a voice channel. |
| `NotInVoiceChannelError` | User is not in a voice channel. |

Example:

```python
from src.core.soundboard import (
    SoundboardError,
    SoundNotFoundError,
    SoundLimitError,
    InvalidSoundFormatError,
    SoundTooLargeError,
    SoundTooLongError,
    InvalidSoundNameError,
    SoundCooldownError,
    PermissionDeniedError,
    ChannelNotFoundError,
)

try:
    soundboard.play_sound(user_id, sound_id, channel_id)
except SoundCooldownError as e:
    print(f"Sound on cooldown for {e.remaining_seconds} more seconds")
except SoundTooLargeError as e:
    print(f"Sound too large: {e.actual_size}/{e.max_size} bytes")
except SoundTooLongError as e:
    print(f"Sound too long: {e.actual_duration}/{e.max_duration} seconds")
except PermissionDeniedError as e:
    print(f"Missing permission: {e.permission}")
```

## Database schema

Tables are prefixed with `soundboard_`:

| Table | Description |
|---|---|
| `soundboard_sounds` | Sound metadata, including `cooldown_seconds`. |
| `soundboard_permissions` | Per-role `can_use` rows. |
| `soundboard_usage` | Usage tracking joined to compute `usage_count`. |
| `soundboard_user_cooldowns` | Per-user cooldown state. |

## Voice module integration

The soundboard triggers playback events; it does not handle audio transport.

1. The caller invokes `play_sound`.
2. The soundboard validates permissions and cooldowns.
3. The soundboard creates a `SoundPlayback` event and records usage.
4. The voice module receives the event and streams audio to the channel.

The voice module is responsible for voice channel connections, audio streaming, and mixing when multiple sounds play at once.

## Files

| File | Description |
|---|---|
| `manager.py` | `SoundboardManager` — upload, permission, and metadata logic. |
| `_playback.py` | `SoundboardPlaybackMixin` — cooldown and `play_sound` implementation. |
| `models.py` | `Sound`, `SoundPermissions`, `SoundFormat`, and related dataclasses. |
| `schema.py` | Pydantic schemas for request validation. |
| `exceptions.py` | `SoundboardError` hierarchy. |
| `__init__.py` | Module `setup` and public re-exports. |
