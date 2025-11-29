# Soundboard Module

Server soundboard system for PlexiChat API supporting sound upload, role-based permissions, cooldowns, and playback triggering for voice channels.

## Features

- Server sound library
- Sound upload with audio validation (MP3/OGG under 5 seconds, under 512KB)
- Sound metadata (name, emoji, volume)
- Sound usage permissions per role
- Sound cooldowns per user
- Play sound in voice channel (triggers event for voice module to handle)
- Usage tracking and statistics
- Format and size validation

## Setup

```python
from src.core.database import Database
from src.core import auth
from src.core import servers
from src.core import soundboard

# Initialize database
db = Database()
db.connect()

# Initialize dependencies
auth.setup(db)
servers.setup(db, auth, messaging)

# Initialize soundboard
soundboard.setup(db, servers)
```

## Usage

### Upload Sound

```python
from src.core import soundboard

# Upload a sound
sound = soundboard.upload_sound(
    user_id=user_id,
    server_id=server_id,
    name="airhorn",
    format=soundboard.SoundFormat.MP3,
    url="https://cdn.example.com/sounds/airhorn.mp3",
    size=245760,
    duration_seconds=2.5,
    emoji="loudspeaker",
    volume=0.8
)
```

### Get Sounds

```python
# Get a specific sound
sound = soundboard.get_sound(sound_id, user_id)

# Get all sounds for a server
sounds = soundboard.get_server_sounds(user_id, server_id)

for sound in sounds:
    print(f"{sound.name}: {sound.duration_seconds}s, used {sound.usage_count} times")
```

### Delete Sound

```python
# Delete a sound (requires server.manage permission)
soundboard.delete_sound(user_id, sound_id)
```

### Sound Permissions

```python
# Allow a role to use a sound
soundboard.set_sound_permissions(
    user_id=admin_id,
    sound_id=sound_id,
    role_id=member_role_id,
    can_use=True
)

# Deny a role from using a sound
soundboard.set_sound_permissions(
    user_id=admin_id,
    sound_id=sound_id,
    role_id=muted_role_id,
    can_use=False
)
```

### Play Sound

```python
# Play a sound in a voice channel
playback = soundboard.play_sound(
    user_id=user_id,
    sound_id=sound_id,
    channel_id=voice_channel_id
)

# The voice module should listen for playback events and handle audio streaming
```

## Configuration

Settings in `config/config.yaml` under `soundboard`:

```yaml
soundboard:
  max_sounds_per_server: 100
  max_sound_size: 524288  # 512KB
  max_sound_duration_seconds: 5
  max_sound_name_length: 30
  allowed_formats:
    - mp3
    - ogg
  default_cooldown_seconds: 5
  max_cooldown_seconds: 300
```

## Sound Formats

| Format | Description | Extension |
|--------|-------------|-----------|
| MP3 | MPEG Audio Layer 3 | .mp3 |
| OGG | Ogg Vorbis | .ogg |

## Permission Integration

For soundboard operations, the module checks:

| Permission | Description |
|------------|-------------|
| server.manage | Required to upload, delete sounds, and set permissions |

## Role-Based Permissions

Sounds can have role-specific permissions:
- By default, all roles can use all sounds
- Admins can restrict specific sounds to specific roles
- Permissions are checked when playing sounds

## Cooldowns

- Each sound has a per-user cooldown (default 5 seconds)
- Prevents spam and abuse
- Cooldown is tracked in memory for performance
- Cooldown starts after sound is played

## Error Handling

All soundboard errors inherit from `SoundboardError`:

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

## Database Schema

Tables (prefixed with `soundboard_`):
- `soundboard_sounds` - Sound metadata
- `soundboard_permissions` - Role-based permissions
- `soundboard_usage` - Usage tracking

## Testing

```bash
pytest src/tests/soundboard/ -v
```

## Integration with Voice Module

The soundboard module triggers playback events that the voice module should handle:

1. User calls `play_sound()`
2. Soundboard validates permissions and cooldowns
3. Soundboard creates `SoundPlayback` event
4. Voice module receives event and streams audio to channel

The voice module is responsible for:
- Actual audio streaming
- Voice channel connection management
- Audio mixing if multiple sounds play simultaneously

## Best Practices

1. **Naming**: Use descriptive names (e.g., "airhorn" not "sound1")
2. **Duration**: Keep sounds under 3 seconds for best UX
3. **Volume**: Set appropriate volume levels (0.5-0.8 recommended)
4. **Format**: Use MP3 for compatibility, OGG for smaller file sizes
5. **Permissions**: Restrict loud or annoying sounds to specific roles
6. **Cooldowns**: Use longer cooldowns for frequently used sounds

## Sound Guidelines

- Maximum size: 512KB
- Maximum duration: 5 seconds
- Recommended duration: 1-3 seconds
- Recommended volume: 0.5-0.8
- Supported formats: MP3, OGG
- Name format: alphanumeric, underscores, hyphens only
