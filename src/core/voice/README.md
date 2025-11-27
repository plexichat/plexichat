# Voice Module

Voice channel state management system for PlexiChat API supporting voice channels, stage channels, and voice state tracking.

## Features

- Voice state management (join, leave, move channels)
- User voice states (self_mute, self_deaf, streaming, video)
- Server moderation (server_mute, server_deaf, move, disconnect)
- Stage channels with speaker/audience management
- Request to speak (raise hand) functionality
- Voice channel settings (user limit, bitrate, region)
- AFK channel with auto-move timeout
- Permission checks integration with servers module

## Setup

```python
from src.core.database import Database
from src.core import auth
from src.core import servers
from src.core import relationships
from src.core import presence
from src.core import voice

# Initialize database
db = Database()
db.connect()

# Initialize dependencies
auth.setup(db)
servers.setup(db, auth)
relationships.setup(db, auth, servers)
presence.setup(db, auth, relationships, servers)

# Initialize voice
voice.setup(db, auth, servers, relationships, presence)
```

## Usage

### Join/Leave Voice Channels

```python
from src.core import voice

# Join a voice channel
state = voice.join_channel(user_id=1, channel_id=123)

# Leave current voice channel
voice.leave_channel(user_id=1)

# Move to a different channel
state = voice.move_to_channel(user_id=1, channel_id=456)

# Get users in a channel
users = voice.get_channel_users(channel_id=123)
```

### Voice State Management

```python
# Set self-mute
state = voice.set_self_mute(user_id=1, muted=True)

# Set self-deaf (also mutes)
state = voice.set_self_deaf(user_id=1, deafened=True)

# Set streaming (screen share)
state = voice.set_streaming(user_id=1, streaming=True)

# Set video (camera)
state = voice.set_video(user_id=1, video=True)

# Update multiple states at once
state = voice.update_voice_state(
    user_id=1,
    self_mute=True,
    streaming=True
)

# Get current voice state
state = voice.get_voice_state(user_id=1)

# Check if user is in voice
in_voice = voice.is_user_in_voice(user_id=1)
```

### Server Moderation

```python
# Server mute a user (requires voice.mute_members permission)
state = voice.server_mute(moderator_id=1, target_user_id=2, server_id=100)
state = voice.server_unmute(moderator_id=1, target_user_id=2, server_id=100)

# Server deafen a user (requires voice.deafen_members permission)
state = voice.server_deaf(moderator_id=1, target_user_id=2, server_id=100)
state = voice.server_undeaf(moderator_id=1, target_user_id=2, server_id=100)

# Move member to different channel (requires voice.move_members permission)
state = voice.move_member(moderator_id=1, target_user_id=2, channel_id=456)

# Disconnect member from voice (requires voice.move_members permission)
voice.disconnect_member(moderator_id=1, target_user_id=2, server_id=100)
```

### Stage Channels

```python
# Start a stage instance
stage = voice.start_stage(user_id=1, channel_id=123, topic="Q&A Session")

# End a stage instance
voice.end_stage(user_id=1, channel_id=123)

# Get active stage
stage = voice.get_stage(channel_id=123)

# Request to speak (raise hand)
request = voice.request_to_speak(user_id=2, channel_id=123)

# Cancel speak request
voice.cancel_speak_request(user_id=2, channel_id=123)

# Invite user to speak (moderator)
state = voice.invite_to_speak(moderator_id=1, target_user_id=2, channel_id=123)

# Move speaker to audience
state = voice.move_to_audience(moderator_id=1, target_user_id=2, channel_id=123)

# Get speakers and audience
speakers = voice.get_speakers(channel_id=123)
audience = voice.get_audience(channel_id=123)
requests = voice.get_speaker_requests(channel_id=123)
```

### Channel Settings

```python
# Set user limit (0 = unlimited)
channel = voice.set_user_limit(user_id=1, channel_id=123, limit=10)

# Set bitrate (8000-384000)
channel = voice.set_bitrate(user_id=1, channel_id=123, bitrate=128000)

# Set voice region (None = automatic)
channel = voice.set_voice_region(user_id=1, channel_id=123, region_id="us-west")

# Get available regions
regions = voice.get_voice_regions()
```

### AFK Channel

```python
# Set AFK channel for server
voice.set_afk_channel(user_id=1, server_id=100, channel_id=789, timeout_seconds=300)

# Get AFK channel
afk_channel_id = voice.get_afk_channel(server_id=100)

# Check and apply AFK timeout (call periodically)
new_state = voice.check_afk_timeout(user_id=2)
```

## Voice States

| State | Description |
|-------|-------------|
| self_mute | User muted themselves |
| self_deaf | User deafened themselves (also mutes) |
| server_mute | Moderator muted the user |
| server_deaf | Moderator deafened the user |
| suppress | Stage channel: user is in audience (not speaking) |
| streaming | User is screen sharing |
| video | User has camera on |

## Channel Types

| Type | Description |
|------|-------------|
| voice | Regular voice channel |
| stage | Stage channel with speakers and audience |

## Permission Integration

| Permission | Description |
|------------|-------------|
| voice.connect | Required to join voice channels |
| voice.speak | Required to speak (not suppressed) |
| voice.mute_members | Required to server mute/unmute |
| voice.deafen_members | Required to server deafen/undeafen |
| voice.move_members | Required to move/disconnect members |
| channels.manage | Required to change channel settings |
| server.manage | Required to set AFK channel |

## Error Handling

All voice errors inherit from `VoiceError`:

```python
from src.core.voice import (
    VoiceError,
    ChannelNotFoundError,
    ChannelAccessDeniedError,
    ChannelFullError,
    ChannelTypeError,
    UserNotInChannelError,
    UserAlreadyInChannelError,
    StageNotFoundError,
    SpeakerRequestNotFoundError,
    SpeakerRequestExistsError,
    NotSpeakerError,
    AlreadySpeakerError,
    PermissionDeniedError,
    InvalidVoiceStateError,
    UserNotFoundError,
)

try:
    voice.join_channel(user_id, channel_id)
except ChannelFullError as e:
    print(f"Channel full: {e.current}/{e.limit}")
except PermissionDeniedError as e:
    print(f"Missing permission: {e.permission}")
except ChannelTypeError as e:
    print(f"Expected {e.expected}, got {e.actual}")
```

## Database Schema

Tables (prefixed with `voice_`):
- `voice_states` - Current voice states for users in channels
- `voice_channel_settings` - Voice channel settings (limit, bitrate, region)
- `voice_stage_instances` - Active stage instances
- `voice_speaker_requests` - Pending speaker requests
- `voice_afk_settings` - AFK channel settings per server

## Voice Regions

Available regions (placeholders for future WebRTC):
- us-west, us-east, us-central, us-south
- eu-west, eu-central
- singapore, japan, brazil, sydney
- automatic (optimal selection)

## Testing

```bash
pytest src/tests/voice/ -v
```

## Notes

- This module handles state management only, not actual audio/video streaming
- Voice regions are placeholders for future WebRTC server selection
- Users can only be in one voice channel at a time
- Joining a new channel automatically leaves the previous one
- Stage channel users start as audience (suppressed)
- Self-deaf automatically enables self-mute
- Server-deaf automatically enables server-mute
