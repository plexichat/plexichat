# Manager Package

## Purpose
Manages voice channel operations, user voice states, moderation (mute/deafen),
stage instances, voice channel settings, and AFK timeouts.

## File Layout

| File           | Mixin / Class          | Responsibilities |
|----------------|------------------------|------------------|
| `__init__.py`  | ÔÇö                      | Re-exports `VoiceManager` |
| `base.py`      | `VoiceManager`         | Composed class; `__init__`, shared helpers (`_get_server_channel`, `_check_permission`, `_row_to_voice_state`, ÔÇª), delegation wrappers for query functions |
| `channels.py`  | `ChannelOpsMixin`      | `join_channel`, `leave_channel`, `move_to_channel`, `get_channel_users`, `get_voice_channel(s)` |
| `state.py`     | `StateMixin`           | `get_voice_state`, `set_self_mute/deaf/stream/video`, `update_voice_state` |
| `moderation.py`| `ModerationMixin`      | `server_mute/unmute`, `server_deaf/undeaf`, `move_member`, `disconnect_member` |
| `stages.py`    | `StageOpsMixin`        | `start/end_stage`, `request/invite/move_to_speak`, `get_speaker_requests/speakers/audience` |
| `settings.py`  | `SettingsMixin`        | `set_user_limit/bitrate/voice_region`, `get_voice_regions` |
| `afk.py`       | `AfkMixin`             | `set/get_afk_channel/timeout`, `check_afk_timeout` |

## Standalone Query Functions

`../queries.py` provides module-level helpers (`is_user_in_voice`, `get_user_channel`,
`get_channel_members`) that accept a `db` argument for use without a manager instance.

## Usage

```python
from src.core.voice.manager import VoiceManager

vm = VoiceManager(db, auth_module=auth, servers_module=servers,
                  relationships_module=relationships, presence_module=presence)

# Join a voice channel
state = vm.join_channel(user_id=1, channel_id=10, server_id=1)

# Mute self
vm.set_self_mute(user_id=1, channel_id=10, muted=True)

# Server mute a user (requires moderation permission)
vm.server_mute(moderator_id=2, target_id=1, server_id=1)

# Start a stage
stage = vm.start_stage(user_id=1, channel_id=10, topic="Town Hall")

# Move user between channels
vm.move_to_channel(moderator_id=2, target_id=1, new_channel_id=11)
```

## Error Handling

Voice operations raise exceptions from `src.core.voice.exceptions`:

- `ChannelNotFoundError` ÔÇö Voice channel ID does not exist.
- `ChannelAccessDeniedError` ÔÇö User lacks permission to join the channel.
- `ChannelFullError` ÔÇö Channel has reached its user limit.
- `ChannelTypeError` ÔÇö Operation invalid for the channel type (e.g., stage-specific ops on a voice channel).
- `UserNotInChannelError` ÔÇö Operation requires user to be in a voice channel.
- `StageNotFoundError` ÔÇö Stage instance not found for the given channel.
- `SpeakerRequestNotFoundError` ÔÇö Speaker request does not exist.
- `SpeakerRequestExistsError` ÔÇö User already has a pending speaker request.
- `NotSpeakerError` ÔÇö Stage operation requires speaker status.
- `AlreadySpeakerError` ÔÇö User is already a speaker in the stage.
- `PermissionDeniedError` ÔÇö User lacks a required permission (includes permission name).
- `InvalidVoiceStateError` ÔÇö Voice state transition is invalid.
- `UserNotFoundError` ÔÇö Target user does not exist.

```python
from src.core.voice.exceptions import (
    ChannelNotFoundError, ChannelFullError, PermissionDeniedError,
    UserNotInChannelError
)

try:
    state = vm.join_channel(user_id=1, channel_id=999)
except ChannelNotFoundError:
    print("Channel not found")
except ChannelFullError:
    print("Channel is full")
except PermissionDeniedError:
    print("Missing CONNECT permission")
```

## Dependencies
- `src.core.base.BaseManager` ÔÇö Database access, ID generation.
- `src.core.servers` ÔÇö Server permission checks, channel lookup.
- `src.core.relationships` ÔÇö Block checks for visibility.
- `src.core.presence` ÔÇö Presence updates when joining/leaving voice.
- Standalone query functions in `../queries.py` for non-manager voice checks.

## Voice Signaling
Voice signaling (WebRTC/SFU) is not handled in this package. See `src.core.voice.signaling`
for SFU backend management (aiortc, mediasoup, Janus) and TURN configuration.
