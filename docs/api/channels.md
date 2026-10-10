# Channels API

Manage channels within a server. You use this API to retrieve, update, delete,
and interact with channels, invites, webhooks, and attachments.

All endpoints in this document are relative to `https://api.plexichat.com`.
For local development, use `http://localhost:8000`.

You must authenticate with `Authorization: Bearer <token>` for all channel
endpoints.

## Channel resource

A channel represents a text, voice, announcement, or stage channel inside a
server. Categories are separate resources that group channels; they are not a
channel type.

```json
{
  "id": "123456789012345678",
  "server_id": "234567890123456789",
  "name": "general",
  "channel_type": "text",
  "topic": "General discussion",
  "position": 0,
  "category_id": null,
  "nsfw": false,
  "slowmode_seconds": 0,
  "read_receipts_enabled": true,
  "created_at": 1704067200
}
```

### Properties

| Field | Type | Description |
| --- | --- | --- |
| `id` | string | Snowflake ID of the channel. |
| `server_id` | string | ID of the parent server. |
| `name` | string | Channel name (1–100 characters). |
| `channel_type` | string | Channel type. One of `text`, `voice`, `announcement`, `stage`. |
| `topic` | string or null | Channel topic. Max 1024 characters. |
| `position` | integer | Display position within the server. |
| `category_id` | string or null | ID of the parent category, if any. |
| `nsfw` | boolean | Whether the channel is marked as NSFW. |
| `slowmode_seconds` | integer | Slowmode delay in seconds (`0` = disabled, `1`–`21600`). |
| `read_receipts_enabled` | boolean | Whether read receipts are tracked in this channel. |
| `created_at` | integer | Unix timestamp in milliseconds when the channel was created. |
| `recipient_id` | string or null | DM only: ID of the other participant. |
| `recipient` | object or null | DM only: Public profile of the recipient. |

### Channel types

| Type | Description |
| --- | --- |
| `text` | Text channel for messages. |
| `voice` | Voice channel for audio communication. |
| `announcement` | Announcement channel for broadcast messages. |
| `stage` | Stage channel for moderated audio events. |

Category is not a channel type. Use `category_id` to place a channel under a
category. DM and group DM channels use `dm` and `group_dm` internally and are
returned with `recipient_id` and `recipient` fields when you fetch a
conversation.

### Slowmode

Slowmode limits how often a user can send messages. `0` disables slowmode.
Values `1`–`21600` set the delay in seconds. The maximum is 6 hours
(21600 seconds).

## Get a channel

Retrieves a single channel.

```
GET /api/v1/channels/{channel_id}
```

You must be a member of the server or a participant in the conversation to
view the channel.

### Example request

```bash
curl -X GET https://api.plexichat.com/api/v1/channels/123456789012345678 \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

### Response

`200 OK` with a channel resource.

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid channel ID format. |
| `401` | Not authenticated. |
| `403` | You do not have access to this channel. |
| `404` | Channel does not exist. |

## Update a channel

Updates channel settings.

```
PATCH /api/v1/channels/{channel_id}
```

Requires the `channels.manage` permission.

### Request body

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string | No | New name, 1–100 characters. |
| `topic` | string | No | New topic, max 1024 characters. |
| `position` | integer | No | New position, `>= 0`. |
| `nsfw` | boolean | No | NSFW flag. |
| `slowmode_seconds` | integer | No | `0`–`21600`. |
| `read_receipts_enabled` | boolean | No | Whether to track read receipts. |

### Example request

```bash
curl -X PATCH https://api.plexichat.com/api/v1/channels/123456789012345678 \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "updated-channel",
    "topic": "New topic",
    "slowmode_seconds": 5
  }'
```

### Response

`200 OK` with the updated channel resource.

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid channel ID or request body. |
| `401` | Not authenticated. |
| `403` | Missing `channels.manage` permission. |
| `404` | Channel does not exist. |

## Delete a channel

Deletes a channel.

```
DELETE /api/v1/channels/{channel_id}
```

Requires the `channels.manage` permission.

### Example request

```bash
curl -X DELETE https://api.plexichat.com/api/v1/channels/123456789012345678 \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

### Response

```json
{
  "success": true
}
```

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid channel ID. |
| `401` | Not authenticated. |
| `403` | Missing `channels.manage` permission. |
| `404` | Channel does not exist. |

## Webhooks

### List channel webhooks

Returns all webhooks for a channel.

```
GET /api/v1/channels/{channel_id}/webhooks
```

Requires the `webhooks.manage` permission.

### Response

`200 OK`

```json
[
  {
    "id": "123456789012345678",
    "channel_id": "123456789012345678",
    "server_id": "234567890123456789",
    "creator_id": "345678901234567890",
    "name": "My Webhook",
    "avatar_url": "https://cdn.example.com/avatars/webhook.png",
    "created_at": 1704067200
  }
]
```

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid channel ID. |
| `401` | Not authenticated. |
| `403` | Missing `webhooks.manage` permission or no access to the channel. |
| `404` | Channel does not exist. |

## Invites

### Create a channel invite

Creates an invite for a channel.

```
POST /api/v1/channels/{channel_id}/invites
```

Requires the `invites.create` permission.

#### Request body

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `max_age` | integer | No | `86400` | Lifetime in seconds. `0` = never expires. |
| `max_uses` | integer | No | `0` | Maximum uses. `0` = unlimited. |
| `temporary` | boolean | No | `false` | Whether the invite grants temporary membership. |

#### Example request

```bash
curl -X POST https://api.plexichat.com/api/v1/channels/123456789012345678/invites \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "max_age": 3600,
    "max_uses": 10
  }'
```

#### Response

`200 OK`

```json
{
  "code": "abc123",
  "channel_id": "123456789012345678",
  "server_id": "234567890123456789",
  "max_age": 3600,
  "max_uses": 10,
  "temporary": false,
  "uses": 0,
  "created_at": 1704067200
}
```

#### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid channel ID or invite parameters. |
| `401` | Not authenticated. |
| `403` | Missing `invites.create` permission or no access to the channel. |
| `404` | Channel does not exist. |

### Get invite information

Returns public information about an invite without joining.

```
GET /api/v1/channels/invites/{invite_code}
```

### Response

`200 OK`

```json
{
  "code": "abc123",
  "server_id": "123456789012345678",
  "server_name": "My Server",
  "channel_id": "234567890123456789",
  "inviter_id": "345678901234567890",
  "uses": 5,
  "max_uses": 100,
  "expires_at": 1704153600
}
```

### Errors

| Status | Description |
| --- | --- |
| `401` | Not authenticated. |
| `404` | Invite does not exist or has expired. |

### Join via invite

Joins a server using an invite code.

```
POST /api/v1/channels/invites/{invite_code}
```

### Response

`200 OK`

```json
{
  "success": true,
  "server_id": "123456789012345678"
}
```

### Errors

| Status | Description |
| --- | --- |
| `401` | Not authenticated. |
| `403` | You are banned from the server. |
| `404` | Invite does not exist or has expired. |
| `409` | You are already a member of the server. |

### Delete an invite

Deletes an invite.

```
DELETE /api/v1/channels/invites/{invite_code}
```

Requires the `invites.manage` or `server.manage` permission.

### Response

```json
{
  "success": true
}
```

### Errors

| Status | Description |
| --- | --- |
| `401` | Not authenticated. |
| `403` | Missing permission to delete the invite. |
| `404` | Invite does not exist. |

## Attachments

### Upload an attachment

Uploads a file attachment to a channel.

```
POST /api/v1/channels/{channel_id}/attachments
Content-Type: multipart/form-data
```

You must have the `messages.send` permission in the channel. You must be a
member of the server or a participant in the conversation.

#### Request

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `file` | file | Yes | File to upload. |

#### Response

`200 OK`

```json
{
  "id": "abc123def456",
  "filename": "image.png",
  "size": 12345,
  "content_type": "image/png",
  "url": "/api/v1/media/attachments/abc123def456",
  "hash": "e3b0c44298fc1c149afbf4c8996fb924...",
  "thumbnails": {
    "small": "/api/v1/media/attachments/abc123def456/thumb_small"
  }
}
```

#### File size limits

Limits are based on your rate limit tier:

| Tier | Limit |
| --- | --- |
| `standard` | 10 MB |
| `alpha` | 25 MB |
| `premium` | 100 MB |

#### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid channel ID, unsupported file type, or file exceeds tier limit. |
| `401` | Not authenticated. |
| `403` | Missing `messages.send` permission or no access to the channel. |
| `404` | Channel does not exist. |

## Related endpoints

*   [Messages](messages.md): Send and manage messages.
*   [Servers](servers.md): Manage servers, members, and roles.
*   [Webhooks](webhooks.md): Manage webhooks in more detail.
