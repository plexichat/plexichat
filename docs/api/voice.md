# Voice

Use the Voice API to discover WebRTC ICE servers and to fetch voice-channel connection metadata.

**Base URL**: `https://api.plexichat.com/api/v1`

For development, use `http://localhost:8000/api/v1`.

All endpoints in this guide are prefixed with `/api/v1/voice` unless noted. The API prefix `api.api_prefix` defaults to `/api/v1` (`src/config_defaults.py:130`).

You must authenticate with `Authorization: Bearer <token>` for all Voice endpoints.

## Error envelope

All errors use the standard envelope (`src/api/schemas/common.py:58`):

```json
{
  "error": {
    "code": 400,
    "message": "Invalid channel ID"
  }
}
```

`code` mirrors the HTTP status. Check `message` for human-readable detail.

## Get ICE servers

### GET /api/v1/voice/ice-servers

Return STUN and TURN servers with credentials for WebRTC negotiation. The server builds the list from your deployment's configured `voice` settings (`src/api/routes/voice.py:50`).

**Headers**

```http
Authorization: Bearer <token>
```

**Response `200 OK`**

```json
{
  "ice_servers": [
    {
      "urls": ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"],
      "username": null,
      "credential": null
    },
    {
      "urls": ["turn:turn.example.com:3478"],
      "username": "turnuser",
      "credential": "turnpass"
    }
  ]
}
```

| Field | Type | Description |
|---|---|---|
| `ice_servers` | array | List of `ICEServerConfig` objects (`src/api/schemas/voice.py:5`) |
| `ice_servers[].urls` | string[] | STUN or TURN URLs |
| `ice_servers[].username` | string or null | TURN username, if configured |
| `ice_servers[].credential` | string or null | TURN credential, if configured |

The endpoint is cached for 300 seconds. If the signaling module is not initialized, the server returns public STUN defaults so clients can still attempt peer-to-peer connections. Other failures surface as real errors and are not masked (`src/api/routes/voice.py:63`).

**Error responses**

| Status | Envelope | Meaning |
|---|---|---|
| `401` | `{"error":{"code":401,"message":"Invalid or expired token"}}` | Missing or invalid session token |
| `500` | `{"error":{"code":500,"message":"Internal server error"}}` | Unexpected server error |

**Example request**

```bash
curl -X GET http://localhost:8000/api/v1/voice/ice-servers \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

## Get voice-channel info

### GET /api/v1/voice/channels/{channel_id}/info

Return connection metadata for a specific voice channel, including the session ID, recommended bitrate, and ICE servers for that channel (`src/api/schemas/voice.py:25`).

**Path parameters**

| Field | Type | Description |
|---|---|---|
| `channel_id` | string | Snowflake ID of the voice channel. Must be numeric; otherwise the server returns `400` |

**Headers**

```http
Authorization: Bearer <token>
```

**Response `200 OK`**

```json
{
  "channel_id": 123456789012345678,
  "session_id": "sess_abc123",
  "ice_servers": [
    {
      "urls": ["stun:stun.l.google.com:19302"],
      "username": null,
      "credential": null
    }
  ],
  "bitrate": 64000
}
```

| Field | Type | Description |
|---|---|---|
| `channel_id` | integer | Voice channel ID |
| `session_id` | string | Session ID for the voice connection |
| `ice_servers` | array | ICE servers for this channel |
| `bitrate` | integer | Recommended bitrate in bits per second |

**Error responses**

| Status | Envelope | Meaning |
|---|---|---|
| `400` | `{"error":{"code":400,"message":"Invalid channel ID"}}` | `channel_id` is not numeric |
| `401` | `{"error":{"code":401,"message":"Invalid or expired token"}}` | Not authenticated |
| `403` | `{"error":{"code":403,"message":"Internal server error"}}` | Not connected to the voice channel or lacking `voice.connect` permission (`src/api/routes/voice.py:171`) |
| `404` | `{"error":{"code":404,"message":"Channel not found"}}` | Voice channel does not exist |
| `500` | `{"error":{"code":500,"message":"Internal server error"}}` | Unexpected error |
| `503` | `{"error":{"code":503,"message":"Voice signaling service currently unavailable"}}` | Signaling service not initialized |

**Example request**

```bash
curl -X GET http://localhost:8000/api/v1/voice/channels/123456789012345678/info \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

## SFU backend

Plexichat routes media through a Selective Forwarding Unit (SFU).

| Backend | Description | Config key |
|---|---|---|
| `aiortc` | Pure-Python WebRTC that runs in-process. Default for development and lightweight deployments (`src/config_defaults.py:396`) | `voice.sfu_backend` |
| `mediasoup` | Native C++ SFU via an external REST API. High performance and independently scalable | `voice.sfu_backend` |
| `janus` | REST adapter for Janus Gateway | `voice.sfu_backend` |

Configure the backend with `voice.sfu_backend` (default: `aiortc`). Other voice keys include `voice.stun_urls`, `voice.turn_urls`, `voice.turn_username`, and `voice.turn_credential` (`src/config_defaults.py:394`).

## Gateway voice signaling

Actual media transport is negotiated outside these REST endpoints. Voice signaling uses the WebSocket gateway at `/gateway` (`src/api/websocket/gateway.py` (`gateway_endpoint`)).

Relevant gateway opcodes (`src/api/websocket/opcodes.py:25`):

| Opcode | Name | Purpose |
|---|---|---|
| `20` | `VOICE_CONNECT` | Join a voice channel |
| `21` | `VOICE_DISCONNECT` | Leave a voice channel |
| `22` | `VOICE_SDP_OFFER` | SDP offer |
| `23` | `VOICE_SDP_ANSWER` | SDP answer |
| `24` | `VOICE_ICE_CANDIDATE` | ICE candidate |
| `25` | `VOICE_SPEAKING` | Speaking state |
| `26` | `VOICE_QUALITY` | Quality update |

Subscribe to voice events through gateway dispatches and combine them with the REST responses above.

## Client guidance

To build a voice-capable client:

1. Request `GET /api/v1/voice/ice-servers` instead of hard-coding STUN or TURN hosts.
2. Call `GET /api/v1/voice/channels/{channel_id}/info` when the user joins a voice channel to obtain the session ID and bitrate.
3. Connect to `wss://your-host/gateway` (or `ws://localhost:8000/gateway` in development) to exchange voice opcodes.
4. Treat ICE and TURN settings as runtime data. Deployments can expose different servers, so do not cache them as static config.
5. Handle `403`, `404`, and `503` gracefully when voice is disabled or a channel is unavailable.
