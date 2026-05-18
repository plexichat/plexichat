# Relationships API

Endpoints for managing friend relationships and user blocks.

**Base URL**: `https://api.plexichat.com/api/v1`

For development, use `http://localhost:8000/api/v1`.

All endpoints in this document are prefixed with `/api/v1/relationships/` unless otherwise specified.

## GET /relationships/@me

Get all relationships for the current user.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
[
  {
    "user_id": "123456789012345678",
    "status": "friend",
    "created_at": 1704067200
  },
  {
    "user_id": "234567890123456789",
    "status": "pending_incoming",
    "created_at": 1704067300
  }
]
```

### Relationship Statuses

- friend: Mutual friendship
- pending_incoming: Incoming friend request
- pending_outgoing: Outgoing friend request
- blocked: User is blocked

## POST /relationships

Send a friend request.

### Headers

```
Authorization: Bearer <token>
```

### Request Body

- `user_id` (string, required, Snowflake ID): Target user ID
- `message` (string, optional, Max 256 chars): Optional message

### Example Request

```json
{
  "user_id": "123456789012345678",
  "message": "Hey, let's be friends!"
}
```

### Response (200 OK)

```json
{
  "user_id": "123456789012345678",
  "status": "pending_outgoing",
  "created_at": 1704067200
}
```

### Error Responses

- 400 Self request: Cannot send request to yourself
- 403 Blocked: User has blocked you
- 404 User not found: User doesn't exist
- 409 Already exists: Request exists or already friends

## PUT /relationships/{user_id}/accept

Accept a pending friend request.

### Headers

```
Authorization: Bearer <token>
```

### Path Parameters

- `user_id` (string): Sender's user ID

### Response (200 OK)

```json
{
  "success": true
}
```

### Error Responses

- 400 Invalid user ID: ID format invalid
- 404 Request not found: No pending request from user

## DELETE /relationships/{user_id}

Remove a relationship. Action depends on current status:
- Friend: Unfriend
- Pending incoming: Decline request
- Pending outgoing: Cancel request
- Blocked: Unblock

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

- 400 Invalid user ID: ID format invalid
- 404 Relationship not found: No relationship exists

## POST /relationships/block

Block a user. Removes any existing relationship.

### Headers

```
Authorization: Bearer <token>
```

### Request Body

- `user_id` (string, required): User ID to block

### Example Request

```json
{
  "user_id": "123456789012345678"
}
```

### Response (200 OK)

```json
{
  "user_id": "123456789012345678",
  "status": "blocked",
  "created_at": 1704067200
}
```

### Error Responses

- 400 Self block: Cannot block yourself
- 404 User not found: User doesn't exist
- 409 Already blocked: User already blocked

## Relationship Object

```json
{
  "user_id": "123456789012345678",
  "status": "friend",
  "created_at": 1704067200
}
```

- `user_id` (string): Related user's ID
- `status` (string): Relationship status
- `created_at` (int?): Unix timestamp of creation
