# Relationships API

Manage friend relationships and user blocks. You use this API to send, accept, and remove relationships.

All endpoints in this document are relative to `https://api.plexichat.com`.
For local development, use `http://localhost:8000`.

You must authenticate with `Authorization: Bearer <token>` for all relationship endpoints.

Rate limits apply per user. The `POST /relationships` route is limited to 10 friend requests per rolling minute; repeated calls return `429`.

Relationship events are dispatched via WebSocket so clients can update contact lists live. If you use caching, cached relationship lists expire after 60 seconds.

## Relationship resource

```json
{
  "user_id": "123456789012345678",
  "username": "Alice",
  "avatar_url": "https://cdn.example.com/avatars/123.png",
  "status": "friend",
  "presence": {"status": "online"},
  "message": "Hey, want to be friends?",
  "created_at": 1704067200
}
```

### Properties

| Field | Type | Description |
| --- | --- | --- |
| `user_id` | string | Snowflake ID of the related user. |
| `username` | string | Username of the related user. Falls back to `User {id}` if the profile cannot be resolved. |
| `avatar_url` | string or null | Avatar URL of the related user. |
| `status` | string | Relationship status. See the statuses table. |
| `presence` | object or null | Current presence, with a `status` of `online`, `idle`, `dnd`, or `offline`. Blocked users always return `offline` to avoid leaking presence. |
| `message` | string or null | Optional message from the original friend request. Present for pending requests. |
| `created_at` | integer or null | Unix timestamp in seconds when the relationship was created. |

### Relationship statuses

| Status | Description |
| --- | --- |
| `friend` | Mutual friendship. |
| `pending_incoming` | Incoming friend request awaiting your action. |
| `pending_outgoing` | Outgoing friend request awaiting the recipient's action. |
| `blocked` | User is blocked. You receive no presence for blocked users. |
| `none` | No relationship (internal only; never returned as a listed relationship). |

## List your relationships

Returns friends, pending requests, and blocked users for the current user. You can page the results.

```
GET /api/v1/relationships/@me
```

Results are cached for 60 seconds and are enriched with live profile and presence data in a single bulk lookup per request.

### Query parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `limit` | integer | No | Maximum relationships to return. `1`–`500`, default `100`. |
| `offset` | integer | No | Offset for pagination, `>= 0`, default `0`. |

### Example request

```bash
curl -X GET "https://api.plexichat.com/api/v1/relationships/@me?limit=100&offset=0" \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

### Response

`200 OK`

```json
[
  {
    "user_id": "123456789012345678",
    "username": "Alice",
    "avatar_url": "https://cdn.example.com/avatars/123.png",
    "status": "friend",
    "presence": {"status": "online"},
    "created_at": 1704067200
  },
  {
    "user_id": "234567890123456789",
    "username": "Bob",
    "avatar_url": null,
    "status": "pending_incoming",
    "presence": {"status": "offline"},
    "message": "Hey, want to be friends?",
    "created_at": 1704067300
  }
]
```

### Errors

| Status | Description |
| --- | --- |
| `401` | Not authenticated. |
| `500` | Relationships module not available or failed to fetch relationships. |

## Send a friend request

Create a pending friend request.

```
POST /api/v1/relationships
```

If a reverse pending request already exists (the target already sent you a request), sending a request auto-accepts the friendship. In that case you and the target both receive a `friend` relationship event, and the API returns `status: friend` instead of `pending_outgoing`.

### Request body

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `user_id` | string | Yes | Snowflake ID of the target user. |
| `message` | string | No | Optional message. Max 256 characters. |

### Example request

```bash
curl -X POST https://api.plexichat.com/api/v1/relationships \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "123456789012345678",
    "message": "Hey, let's be friends!"
  }'
```

### Response

`200 OK`

```json
{
  "user_id": "123456789012345678",
  "status": "pending_outgoing",
  "created_at": 1704067200
}
```

When auto-accepted:

```json
{
  "user_id": "123456789012345678",
  "status": "friend",
  "created_at": 1704067200
}
```

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid user ID, sending a request to yourself, or validation error. |
| `401` | Not authenticated. |
| `403` | Cannot send the request because a block exists. The message is intentionally generic to avoid revealing block direction. |
| `404` | Target user does not exist. |
| `409` | Request already exists or you are already friends. |
| `429` | Too many friend requests. Slow down. |

## Accept a friend request

Accept a pending incoming friend request.

```
PUT /api/v1/relationships/{user_id}/accept
```

You specify the sender's user ID, not the request ID. The API looks up the pending incoming request from that sender.

### Path parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `user_id` | string | Yes | Snowflake ID of the user who sent the request. |

### Example request

```bash
curl -X PUT https://api.plexichat.com/api/v1/relationships/123456789012345678/accept \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

### Response

`200 OK`

```json
{
  "success": true
}
```

Both users receive a `friend` relationship event that includes live usernames and visible presence.

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid user ID format. |
| `401` | Not authenticated. |
| `404` | Pending request not found. |
| `500` | Failed to accept the request. |

## Remove a relationship

Remove a relationship. The action depends on the current status.

```
DELETE /api/v1/relationships/{user_id}
```

| Current status | Action |
| --- | --- |
| `friend` | Remove the friendship from both users. |
| `pending_incoming` | Decline the incoming request. |
| `pending_outgoing` | Cancel the outgoing request. |
| `blocked` | Unblock the user. |

This is the same as `DELETE /api/v1/relationships/{user_id:int}`. The `user_id` must be a valid integer Snowflake ID.

### Path parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `user_id` | string | Yes | Snowflake ID of the related user. |

### Example request

```bash
curl -X DELETE https://api.plexichat.com/api/v1/relationships/123456789012345678 \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

### Response

`200 OK`

```json
{
  "success": true
}
```

Both users receive a `remove` relationship event when the relationship is a friendship or a pending request.

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid user ID format. |
| `401` | Not authenticated. |
| `404` | Relationship does not exist. |
| `500` | Failed to delete the relationship. |

## Block a user

Block a user. If a friendship or pending request exists, it is removed.

```
POST /api/v1/relationships/block
```

If the user was previously a friend, the target receives a `remove` event for your user.

### Request body

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `user_id` | string | Yes | Snowflake ID of the user to block. |

### Example request

```bash
curl -X POST https://api.plexichat.com/api/v1/relationships/block \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "123456789012345678"
  }'
```

### Response

`200 OK`

```json
{
  "user_id": "123456789012345678",
  "status": "blocked",
  "created_at": 1704067200
}
```

You receive an `add` relationship event with `status: blocked`.

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid target ID or cannot block yourself. |
| `401` | Not authenticated. |
| `404` | User does not exist. |
| `409` | User is already blocked. |

## Related endpoints

*   [Users](users.md) — User profiles and search.
*   [WebSocket Events](../websocket/events.md) — `RELATIONSHIP_ADD` and `RELATIONSHIP_REMOVE` events.
