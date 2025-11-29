# Webhooks API

Endpoints for webhook management and execution.

## POST /webhooks

Create a new webhook for a channel.

### Headers

```
Authorization: Bearer <token>
```

### Request Body

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| channel_id | string | Yes | Snowflake ID | Target channel |
| name | string | Yes | 1-80 chars | Webhook name |
| avatar_url | string | No | Valid URL | Webhook avatar |

### Example Request

```json
{
  "channel_id": "123456789012345678",
  "name": "My Webhook",
  "avatar_url": "https://cdn.example.com/avatars/webhook.png"
}
```

### Response (200 OK)

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
  "created_at": 1704067200
}
```

**Important:** Token and URL are only returned on creation. Store them securely.

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 400 | Invalid input | Validation failed |
| 400 | Webhook limit | Channel webhook limit reached |
| 403 | Permission denied | No manage webhooks permission |
| 404 | Channel not found | Channel doesn't exist |

## GET /webhooks/{webhook_id}

Get webhook details (without token).

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

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
  "created_at": 1704067200
}
```

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 400 | Invalid webhook ID | ID format invalid |
| 403 | Access denied | No permission to view |
| 404 | Webhook not found | Webhook doesn't exist |

## DELETE /webhooks/{webhook_id}

Delete a webhook.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
{
  "success": true
}
```

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 403 | Permission denied | No manage webhooks permission |
| 404 | Webhook not found | Webhook doesn't exist |

## POST /webhooks/{webhook_id}/{token}

Execute a webhook to send a message. No authentication required if token is valid.

### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| webhook_id | string | Webhook's snowflake ID |
| token | string | Webhook token |

### Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| wait | bool | false | Wait for message and return it |

### Request Body

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| content | string | Conditional | Max 2000 chars | Message content |
| username | string | No | Max 80 chars | Override webhook name |
| avatar_url | string | No | Valid URL | Override webhook avatar |
| embeds | array | No | - | Rich embeds |
| thread_id | string | No | Snowflake ID | Thread to post to |

At least one of `content` or `embeds` is required.

### Example Request

```json
{
  "content": "Hello from webhook!",
  "username": "Custom Bot Name",
  "avatar_url": "https://cdn.example.com/custom-avatar.png"
}
```

### Response (200 OK with wait=false)

```
null
```

### Response (200 OK with wait=true)

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

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 400 | Empty message | No content or embeds |
| 400 | Invalid content | Content validation failed |
| 401 | Invalid token | Webhook token invalid |
| 404 | Webhook not found | Webhook doesn't exist |

## Webhook Object

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
  "created_at": 1704067200
}
```

| Field | Type | Description |
|-------|------|-------------|
| id | string | Webhook's snowflake ID |
| channel_id | string | Target channel ID |
| server_id | string | Server ID |
| creator_id | string | Creator's user ID |
| name | string | Webhook name |
| avatar_url | string? | Webhook avatar URL |
| token | string? | Webhook token (only on create) |
| url | string? | Full webhook URL (only on create) |
| created_at | int | Unix timestamp of creation |
