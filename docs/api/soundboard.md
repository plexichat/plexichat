# Soundboard API

Server sound library endpoints for uploading, managing, and playing short audio clips in voice channels.

## Overview

Each server has a soundboard of sounds usable by its members. All manage operations (upload, delete, metadata changes, role permissions) require the `server.soundboard.manage` scope (users with `server.manage` also qualify). Listing requires server membership; playback is allowed for members permitted by the sound's role rules.

Uploaded audio bytes are validated server-side with magic-byte container detection against the configured allow-list (`audio/mpeg`, `audio/ogg` by default) and stored through the shared media pipeline. Client-declared URL/size/duration values are never trusted. The default size limit is 2 MiB (`soundboard.max_sound_size`); requests declaring or delivering larger bodies are rejected with HTTP 413.

All endpoints require authentication via `Authorization: Bearer <token>`.

## Endpoints

### List Server Sounds

```
GET /api/v1/servers/{server_id}/soundboard
```

Returns all sounds in the server's soundboard (members only).

**Errors:** `400` invalid server ID, `403` not a member.

**Response:** `SoundResponse[]`

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

### Upload a Sound

```
POST /api/v1/servers/{server_id}/soundboard
Content-Type: multipart/form-data
```

Uploads raw audio bytes as a new server sound. Bytes are container-detected and matched against the configured allowed formats; the stored URL and size are derived from the media pipeline result.

**Form fields:**

| Field              | Type   | Required | Notes                                  |
|--------------------|--------|----------|----------------------------------------|
| `name`             | string | yes      | 1–64 characters                        |
| `file`             | file   | yes      | Audio bytes (MPEG/Ogg/WAVE/WebM detected) |
| `emoji`            | string | no       | Related unicode emoji                  |
| `volume`           | float  | no       | Default volume, clamped to 0.0–1.0 (default `1.0`) |
| `duration_seconds` | float  | no       | Hint only; clamped to configured max duration |

**Errors:** `400` invalid upload/format/name, `403` missing `server.soundboard.manage`, `404` server not found, `413` file too large.

**Response:** `SoundResponse`

### Get a Sound

```
GET /api/v1/servers/{server_id}/soundboard/{sound_id}
```

Fetches a single sound.

**Response:** `SoundResponse`

### Update Sound Metadata

```
PATCH /api/v1/servers/{server_id}/soundboard/{sound_id}
```

Updates editable metadata fields. All fields optional.

**Body (`SoundUpdateRequest`):**

```json
{
  "name": "new-name",
  "emoji": "🔥",
  "volume": 0.8,
  "cooldown": 5
}
```

| Field      | Type   | Constraints            |
|------------|--------|------------------------|
| `name`     | string | 1–64 characters        |
| `emoji`    | string | up to 32 characters    |
| `volume`   | float  | 0.0–1.0                |
| `cooldown` | int    | >= 0 seconds (clamped to configured max) |

**Response:** `SoundResponse`

### Delete a Sound

```
DELETE /api/v1/servers/{server_id}/soundboard/{sound_id}
```

Removes a sound (requires `server.soundboard.manage`).

**Errors:** `403` missing permission, `404` sound/server not found.

**Response:**

```json
{ "success": true }
```

### Set Role Playback Permission

```
PUT /api/v1/servers/{server_id}/soundboard/{sound_id}/permissions/{role_id}
```

Allows or denies a role from playing this sound (manage scope required). The role must belong to the sound's server.

**Body (`SoundPermissionRequest`):**

```json
{ "can_use": true }
```

**Response (`SoundPermissionResponse`):**

```json
{
  "id": "222333444555666777",
  "sound_id": "123456789012345678",
  "role_id": "333444555666777888",
  "can_use": true
}
```

### Play a Sound

```
POST /api/v1/servers/{server_id}/soundboard/{sound_id}/play
```

Records playback of a sound in one of the caller's voice channels. Per-user cooldowns are enforced atomically; an immediate replay is rejected with HTTP 429.

**Body (`SoundPlayRequest`):**

```json
{
  "channel_id": "444555666777888999",
  "volume": 0.9
}
```

| Field        | Type   | Required | Constraints |
|--------------|--------|----------|-------------|
| `channel_id` | string | yes      | Voice channel the user is connected to |
| `volume`     | float  | no       | 0.0–1.0     |

**Response (`SoundPlaybackResponse`):**

```json
{
  "sound": { "...": "SoundResponse" },
  "user_id": "111222333444555666",
  "channel_id": "444555666777888999",
  "timestamp": 1704067200000
}
```

**Errors:** `400` not in a voice channel / invalid payload, `403` not allowed to use this sound, `404` sound/channel/server not found, `429` sound is on cooldown.

## Error Format

Failures return the standard error envelope:

```json
{
  "error": {
    "code": 403,
    "message": "Missing server.soundboard.manage permission"
  }
}
```

Unexpected internal failures return HTTP 500 with a static message; details are logged server-side only.
