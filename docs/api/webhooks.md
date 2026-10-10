# Webhooks

Use webhooks to create inbound integrations that post messages into Plexichat channels. A webhook is bound to one channel and authenticates with its own token.

**Base URL**: `https://api.plexichat.com/api/v1`

For development, use `http://localhost:8000/api/v1`.

All endpoints in this guide are prefixed with `/api/v1/webhooks` unless noted. The API prefix `api.api_prefix` defaults to `/api/v1` (`src/config_defaults.py:131`).

## Error envelope

All errors use the standard envelope (`src/api/schemas/common.py:58`):

```json
{
  "error": {
    "code": 400,
    "message": "Invalid webhook ID"
  }
}
```

`code` mirrors the HTTP status.

## Webhook object

```json
{
  "id": "123456789012345678",
  "channel_id": "123456789012345678",
  "server_id": "123456789012345678",
  "creator_id": "123456789012345678",
  "name": "My Webhook",
  "avatar_url": "https://cdn.example.com/avatars/webhook.png",
  "token": "webhook_token_here",
  "url": "https://api.example.com/api/v1/webhooks/123456789012345678/webhook_token_here",
  "require_signed_deliveries": false,
  "created_at": 1704067200
}
```

| Field | Type | Description |
|---|---|---|
| `id` | string | Snowflake ID |
| `channel_id` | string | Channel that owns the webhook |
| `server_id` | string | Server that owns the channel |
| `creator_id` | string | User who created the webhook |
| `name` | string | 1–80 characters (`src/api/schemas/webhooks.py:17`) |
| `avatar_url` | string or null | Valid URL or null |
| `token` | string or null | Returned only on create and on token regeneration (`src/api/routes/webhooks.py:41`) |
| `url` | string or null | Full execution URL, returned only with the token |
| `require_signed_deliveries` | boolean | When `true`, executions without a valid Ed25519 signature are rejected even with a valid token (`src/api/schemas/webhooks.py:19`) |
| `created_at` | integer | Unix timestamp |

## Create a webhook

### POST /api/v1/webhooks

Create a webhook for a channel. Only the creation response includes the token and URL. Store them securely; subsequent reads omit them.

**Headers**

```http
Authorization: Bearer <token>
```

**Request body**

| Field | Type | Required | Description |
|---|---|---|---|
| `channel_id` | string | Yes | Snowflake ID of the target channel |
| `name` | string | Yes | 1–80 characters |
| `avatar_url` | string | No | Valid URL |
| `require_signed_deliveries` | boolean | No | Default `false`. When `true`, the execute endpoint requires `X-Plexichat-Signature` and `X-Plexichat-Timestamp` |

**Example request**

```bash
curl -X POST http://localhost:8000/api/v1/webhooks \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "channel_id": "123456789012345678",
    "name": "My Webhook",
    "avatar_url": "https://cdn.example.com/avatars/webhook.png"
  }'
```

**Response `200 OK`**

Returns a `WebhookResponse`. `token` and `url` are populated only on create.

**Error responses**

| Status | Envelope | Meaning |
|---|---|---|
| `400` | `{"error":{"code":400,"message":"Invalid webhook name, avatar or limit reached"}}` | Validation failed or per-channel limit (default 10) reached |
| `401` | `{"error":{"code":401,"message":"Invalid or expired token"}}` | Not authenticated |
| `403` | `{"error":{"code":403,"message":"Permission denied"}}` | Lacks `manage webhooks` permission |
| `404` | `{"error":{"code":404,"message":"Channel not found"}}` | Channel does not exist |

## Get a webhook

### GET /api/v1/webhooks/{webhook_id}

Return webhook details without the token.

**Headers**

```http
Authorization: Bearer <token>
```

**Path parameters**

| Field | Type | Description |
|---|---|---|
| `webhook_id` | string | Snowflake ID |

**Response `200 OK`**

```json
{
  "id": "123456789012345678",
  "channel_id": "123456789012345678",
  "server_id": "123456789012345678",
  "creator_id": "123456789012345678",
  "name": "My Webhook",
  "avatar_url": "https://cdn.example.com/avatars/webhook.png",
  "token": null,
  "url": null,
  "require_signed_deliveries": false,
  "created_at": 1704067200
}
```

**Error responses**

| Status | Meaning |
|---|---|
| `400` | Invalid webhook ID format |
| `401` | Not authenticated |
| `403` | No permission to view |
| `404` | Webhook not found |

## Update a webhook

### PATCH /api/v1/webhooks/{webhook_id}

Change the webhook name, avatar, channel, or signed-delivery requirement (`src/api/routes/webhooks.py:287`).

**Headers**

```http
Authorization: Bearer <token>
```

**Request body**

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | No | New name, 1–80 characters |
| `avatar_url` | string | No | New avatar URL |
| `channel_id` | string | No | Move the webhook to another channel |
| `require_signed_deliveries` | boolean | No | Toggle signed-delivery enforcement |

**Response `200 OK`**

Returns the updated `WebhookResponse` without the token. The update also emits a `WEBHOOKS_UPDATE` gateway event (`src/api/routes/webhooks.py:344`).

**Error responses**

| Status | Meaning |
|---|---|
| `400` | Invalid webhook ID or name too long |
| `403` | Permission denied |
| `404` | Webhook or target channel not found |

## Regenerate the webhook token

### POST /api/v1/webhooks/{webhook_id}/regenerate-token

Rotate the token. The old token is invalidated and the updated webhook with the new token is returned (`src/api/routes/webhooks.py:59`).

**Headers**

```http
Authorization: Bearer <token>
```

**Response `200 OK`**

Returns a `WebhookResponse` with `token` and `url` populated.

**Error responses**

| Status | Meaning |
|---|---|
| `400` | Invalid webhook ID |
| `403` | Permission denied |
| `404` | Webhook not found |

## Delete a webhook

### DELETE /api/v1/webhooks/{webhook_id}

Delete a webhook.

**Headers**

```http
Authorization: Bearer <token>
```

**Response `200 OK`**

```json
{
  "success": true
}
```

**Error responses**

| Status | Meaning |
|---|---|
| `400` | Invalid webhook ID |
| `403` | Permission denied |
| `404` | Webhook not found |

## Execute a webhook

### POST /api/v1/webhooks/{webhook_id}/{token}

Send a message through the webhook. No `Authorization` header is required when the token is valid. Use the `wait` query parameter to control the response shape.

**Path parameters**

| Field | Type | Description |
|---|---|---|
| `webhook_id` | string | Snowflake ID |
| `token` | string | Webhook token from create or regenerate |

**Query parameters**

| Field | Type | Description |
|---|---|---|
| `wait` | boolean | Default `false`. When `true`, the server returns the created message; otherwise it returns `{"success":true}` |
| `thread_id` | string | Optional thread ID as a query parameter. If provided in both the query and body, the query value takes precedence |

**Headers**

| Header | Required | Description |
|---|---|---|
| `X-Plexichat-Signature` | Only when `require_signed_deliveries` is `true` or when you want verification | Ed25519 signature of the payload |
| `X-Plexichat-Timestamp` | Only with `X-Plexichat-Signature` | Timestamp used in the signature |

When `require_signed_deliveries` is `true`, unsigned requests are rejected with `401` even if the token is valid (`src/api/routes/webhooks.py:540`).

**Request body**

| Field | Type | Required | Description |
|---|---|---|---|
| `content` | string | No* | 0–2000 characters |
| `username` | string | No | Override the webhook name, 0–80 characters |
| `avatar_url` | string | No | Override the webhook avatar, must be a valid URL |
| `embeds` | array | No | Rich embeds (max 10 per message) |
| `thread_id` | string | No | Snowflake ID of the thread to post to |

*At least one of `content` or `embeds` is required.

**Example request**

```bash
curl -X POST "http://localhost:8000/api/v1/webhooks/123456789012345678/webhook_token_here?wait=true" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Hello from webhook!",
    "username": "Custom Bot Name",
    "avatar_url": "https://cdn.example.com/custom-avatar.png"
  }'
```

**Response `200 OK` with `wait=false`**

```json
{
  "success": true
}
```

**Response `200 OK` with `wait=true`**

```json
{
  "id": "123456789012345678",
  "webhook_id": "123456789012345678",
  "channel_id": "123456789012345678",
  "content": "Hello from webhook!",
  "username": "Custom Bot Name",
  "avatar_url": "https://cdn.example.com/custom-avatar.png",
  "created_at": 1704067200
}
```

**Error responses**

| Status | Envelope | Meaning |
|---|---|---|
| `400` | `{"error":{"code":400,"message":"Invalid webhook execution request"}}` | Empty message, invalid content, or invalid thread ID |
| `401` | `{"error":{"code":401,"message":"Invalid webhook token"}}` | Token invalid, signature invalid, or unsigned delivery rejected when `require_signed_deliveries` is enabled |
| `404` | `{"error":{"code":404,"message":"Webhook not found"}}` | Webhook does not exist |
| `500` | `{"error":{"code":500,"message":"Internal server error"}}` | Unexpected error |
