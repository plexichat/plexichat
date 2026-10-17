# Soundboard API

Manage and play short audio clips in voice channels. Each server has its own soundboard.

**Base URL**: `https://api.plexichat.com/api/v1`

All paths below are prefixed with `/api/v1` unless noted. All endpoints require `Authorization: Bearer <token>`.

## Overview

| Capability | Who can do it | Permission check |
|------------|---------------|------------------|
| List sounds, get a sound | Members of the server | Membership check in the core manager |
| Upload, delete, update metadata, set role permissions | Managers | `server.soundboard.manage` or `server.manage` (`plexichat/src/api/routes/soundboard.py:139`) |
| Play a sound | Any member allowed by the sound's role rules | Checked in the core manager; per-user cooldown is enforced atomically |

Uploads are validated with magic-byte container detection against the configured allow-list. Client-declared `url`, `size`, and `duration` are never trusted — the media pipeline derives them.

### Storage and validation

- Allowed containers by default: `audio/mpeg`, `audio/ogg`, `audio/wav`, `audio/webm` (from `soundboard.allowed_formats` in `plexichat/src/config_defaults.py:651`). The detector inspects bytes, not filenames.
- Size limit: `soundboard.max_sound_size`, default 2 MiB (2,097,152 bytes). The route checks both the declared `UploadFile.size` and the actual bytes. Larger requests get `413`.
- HTTP upload ceiling: 10 MiB (`_HTTP_UPLOAD_CEILING_BYTES` in `plexichat/src/api/routes/soundboard.py:48`). Requests that exceed the ceiling before reaching the manager also get `413`.
- Volume is clamped to `0.0–1.0` on upload and on playback.
- Duration hint is clamped to `soundboard.max_sound_duration_seconds` (default 10 seconds) and `max_cooldown_seconds` (default 30 seconds).

If the soundboard or servers module isn't initialized, manage checks and reads return `503` instead of leaking a stack trace (`plexichat/src/api/routes/soundboard.py:84`).

## Endpoints

### List server sounds

```
GET /api/v1/servers/{server_id}/soundboard
```

Returns all sounds for a server. Members only.

- **Path parameters**: `server_id` — snowflake string. Non-numeric IDs return `400 Invalid server ID`.
- **Errors**: `400` invalid server ID, `403` not a member (mapped from `PermissionDeniedError`), `503` soundboard module not available.
- **Response**: `200 OK` with `SoundResponse[]`.

```json
[
  {
    "id": "123456789012345678",
    "server_id": "987654321098765432",
    "name": "airhorn",
    "format": "mp3",
    "emoji": "📢",
    "url": "/api/v1/media/soundboard/abc123.mp3",
    "size": 148210,
    "duration_seconds": 1.8,
    "volume": 1.0,
    "cooldown_seconds": 0,
    "created_by": "111222333444555666",
    "created_at": 1704067200000,
    "usage_count": 42
  }
]
```

### Upload a sound

```
POST /api/v1/servers/{server_id}/soundboard
Content-Type: multipart/form-data
```

Uploads raw audio bytes as a new server sound.

| Form field | Type | Required | Constraints |
|------------|------|----------|-------------|
| `name` | string | Yes | 1–64 characters (`soundboard.max_sound_name_length`) |
| `file` | file | Yes | Audio bytes; format is container-detected |
| `emoji` | string | No | Unicode emoji; stored as provided |
| `volume` | float | No | Clamped to `0.0–1.0` (default `1.0`) |
| `duration_seconds` | float | No | Hint only; clamped to configured max duration |

Example with `curl`:

```bash
curl -X POST https://api.plexichat.com/api/v1/servers/987654321098765432/soundboard \
  -H "Authorization: Bearer <token>" \
  -F "name=airhorn" \
  -F "file=@airhorn.mp3" \
  -F "emoji=📢" \
  -F "volume=1.0"
```

**Behavior**:

1. The route checks `server.soundboard.manage` at the HTTP layer and the manager re-checks. If the servers module is missing, the check fails with `503` instead of passing silently (`plexichat/src/api/routes/soundboard.py:118`).
2. If the declared or actual size exceeds `soundboard.max_sound_size`, the server returns `413 Sound file is too large`.
3. Bytes are read fully into memory, checked against the HTTP ceiling, then passed to `soundboard.create_sound_from_upload` with a clamped volume. The returned `url` and `size` come from the media pipeline.

**Errors**:

| Status | Condition |
|--------|-----------|
| `400` | Invalid name, format, or payload (`InvalidSoundNameError`, `InvalidSoundFormatError`, `SoundTooLongError`, etc. mapped to `400 Invalid request`) |
| `403` | Missing `server.soundboard.manage` |
| `404` | Server not found |
| `413` | File too large (declared size, actual size, or HTTP ceiling) |
| `503` | Soundboard not initialized or media storage unavailable |

**Response**: `200 OK` with `SoundResponse`.

### Get a sound

```
GET /api/v1/servers/{server_id}/soundboard/{sound_id}
```

Fetches one sound. Both `server_id` and `sound_id` are validated as integers; invalid values return `400`.

- **Errors**: `400` invalid ID, `404` sound not found, `503` module not available.
- **Response**: `200 OK` with `SoundResponse`.

### Update sound metadata

```
PATCH /api/v1/servers/{server_id}/soundboard/{sound_id}
```

Updates editable fields. All fields are optional.

**Body** (`SoundUpdateRequest` in `plexichat/src/api/schemas/soundboard.py:40`):

```json
{
  "name": "new-name",
  "emoji": "🔥",
  "volume": 0.8,
  "cooldown": 5
}
```

| Field | Type | Constraints |
|-------|------|-------------|
| `name` | string | 1–64 characters |
| `emoji` | string | Up to 32 characters |
| `volume` | float | `0.0–1.0` |
| `cooldown` | integer | `>= 0` (clamped to `soundboard.max_cooldown_seconds`) |

The route handles permission checks inside the manager for this endpoint; core errors map to `400`, `403`, `404`, or `429`.

**Response**: `200 OK` with `SoundResponse`.

### Delete a sound

```
DELETE /api/v1/servers/{server_id}/soundboard/{sound_id}
```

Removes a sound. Requires `server.soundboard.manage`.

- **Errors**: `400` invalid ID, `403` missing permission, `404` sound or server not found, `503` module not available.
- **Response**: `200 OK`

```json
{
  "success": true
}
```

### Set role playback permission

```
PUT /api/v1/servers/{server_id}/soundboard/{sound_id}/permissions/{role_id}
```

Allows or denies a role from playing this sound. The role must belong to the sound's server.

**Body** (`SoundPermissionRequest`):

```json
{
  "can_use": true
}
```

**Response** (`SoundPermissionResponse`):

```json
{
  "id": "222333444555666777",
  "sound_id": "123456789012345678",
  "role_id": "333444555666777888",
  "can_use": true
}
```

- **Errors**: `400` invalid ID or payload, `403` missing permission, `404` sound/role/server not found.

### Play a sound

```
POST /api/v1/servers/{server_id}/soundboard/{sound_id}/play
```

Records playback of a sound in one of the caller's voice channels. Per-user cooldowns are enforced atomically — an immediate replay returns `429`.

**Body** (`SoundPlayRequest` in `plexichat/src/api/schemas/soundboard.py:55`):

```json
{
  "channel_id": "444555666777888999",
  "volume": 0.9
}
```

| Field | Type | Required | Constraints |
|-------|------|----------|-------------|
| `channel_id` | string | Yes | Voice channel that the user is connected to |
| `volume` | float | No | `0.0–1.0`; if omitted, the sound's default volume is used |

**Response** (`SoundPlaybackResponse`):

```json
{
  "sound": {
    "id": "123456789012345678",
    "server_id": "987654321098765432",
    "name": "airhorn",
    "format": "mp3",
    "emoji": "📢",
    "url": "/api/v1/media/soundboard/abc123.mp3",
    "size": 148210,
    "duration_seconds": 1.8,
    "volume": 1.0,
    "cooldown_seconds": 3,
    "created_by": "111222333444555666",
    "created_at": 1704067200000,
    "usage_count": 43
  },
  "user_id": "111222333444555666",
  "channel_id": "444555666777888999",
  "timestamp": 1704067200000
}
```

**Errors**:

| Status | Condition |
|--------|-----------|
| `400` | Invalid payload or not in a voice channel (`NotInVoiceChannelError` → `400 Invalid request`) |
| `403` | Not allowed to use this sound |
| `404` | Sound, channel, or server not found |
| `429` | Sound is on cooldown (`SoundCooldownError` → `429 Rate limit exceeded`) |

## Error format

Failures use the standard error envelope:

```json
{
  "error": {
    "code": 403,
    "message": "Missing server.soundboard.manage permission"
  }
}
```

Core soundboard errors are translated in `plexichat/src/api/routes/soundboard.py:154`:

| Core error | HTTP status | Message |
|------------|-------------|---------|
| `SoundNotFoundError`, `ServerNotFoundError` | `404` | `Not found` |
| `ChannelNotFoundError` | `404` | `Not found` |
| `NotInVoiceChannelError` | `400` | `Invalid request` |
| `PermissionDeniedError` | `403` | `Access denied` |
| `SoundCooldownError` | `429` | `Rate limit exceeded` |
| `InvalidSoundFormatError`, `InvalidSoundNameError`, `SoundTooLargeError`, `SoundTooLongError`, `SoundLimitError` | `400` | `Invalid request` |
| Other `SoundboardError` | `400` | `Invalid request` |
| Unhandled exception | `500` | `Soundboard operation failed` (details logged server-side) |
