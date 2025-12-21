# WebSocket Events

Events are dispatched via opcode 0 (DISPATCH).

## Event Structure

```json
{
  "op": 0,
  "t": "EVENT_NAME",
  "s": 1,
  "d": {}
}
```

| Field | Type | Description |
|-------|------|-------------|
| op | int | Always 0 for DISPATCH |
| t | string | Event name |
| s | int | Sequence number |
| d | object | Event data |

## Connection Events

### READY

Sent after successful IDENTIFY.

```json
{
  "t": "READY",
  "d": {
    "user": {
      "id": "123456789012345678",
      "username": "johndoe"
    },
    "session_id": "session_id_here",
    "resume_gateway_url": "wss://gateway.example.com/gateway"
  }
}
```

### RESUMED

Sent after successful RESUME.

```json
{
  "t": "RESUMED",
  "d": {}
}
```

## Message Events

### MESSAGE_CREATE

Sent when a message is created.

```json
{
  "t": "MESSAGE_CREATE",
  "d": {
    "id": "123456789012345678",
    "channel_id": "123456789012345678",
    "author_id": "123456789012345678",
    "content": "Hello, world!",
    "created_at": 1704067200
  }
}
```

### MESSAGE_UPDATE

Sent when a message is edited.

```json
{
  "t": "MESSAGE_UPDATE",
  "d": {
    "id": "123456789012345678",
    "channel_id": "123456789012345678",
    "content": "Updated content",
    "edited_at": 1704067300
  }
}
```

### MESSAGE_DELETE

Sent when a message is deleted.

```json
{
  "t": "MESSAGE_DELETE",
  "d": {
    "id": "123456789012345678",
    "channel_id": "123456789012345678"
  }
}
```

## Presence Events

### PRESENCE_UPDATE

Sent when a user's presence changes. This event is dispatched to all friends of the user when:
- User comes online (connects to gateway)
- User goes offline (disconnects from gateway)
- User changes their status via the API or gateway
- User updates their custom status

```json
{
  "t": "PRESENCE_UPDATE",
  "d": {
    "user_id": "123456789012345678",
    "status": "online",
    "custom_status": "Working",
    "custom_emoji": ":computer:"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| user_id | string | ID of the user whose presence changed |
| status | string | New status: `online`, `idle`, `dnd`, `offline` |
| custom_status | string? | Custom status text (optional) |
| custom_emoji | string? | Custom status emoji (optional) |

Note: Users with `invisible` status appear as `offline` to others.

## Reaction Events

### MESSAGE_REACTION_ADD

Sent when a reaction is added.

```json
{
  "t": "MESSAGE_REACTION_ADD",
  "d": {
    "user_id": "123456789012345678",
    "channel_id": "123456789012345678",
    "message_id": "123456789012345678",
    "emoji": ":thumbsup:"
  }
}
```

### MESSAGE_REACTION_REMOVE

Sent when a reaction is removed.

```json
{
  "t": "MESSAGE_REACTION_REMOVE",
  "d": {
    "user_id": "123456789012345678",
    "channel_id": "123456789012345678",
    "message_id": "123456789012345678",
    "emoji": ":thumbsup:"
  }
}
```

## Server Events

### GUILD_CREATE

Sent when joining a server or on READY.

### GUILD_UPDATE

Sent when server settings change.

### GUILD_DELETE

Sent when leaving or kicked from a server.

## Channel Events

### CHANNEL_CREATE

Sent when a channel is created.

### CHANNEL_UPDATE

Sent when a channel is updated.

### CHANNEL_DELETE

Sent when a channel is deleted.

## Member Events

### GUILD_MEMBER_ADD

Sent when a user joins a server.

### GUILD_MEMBER_UPDATE

Sent when a member is updated.

### GUILD_MEMBER_REMOVE

Sent when a user leaves a server.

## Relationship Events

### RELATIONSHIP_ADD

Sent when a relationship is created or updated. This includes:
- Friend request sent (recipient receives `pending_incoming`, sender receives `pending_outgoing`)
- Friend request accepted (both users receive `friend` status)
- User blocked (blocker receives `blocked` status)

```json
{
  "t": "RELATIONSHIP_ADD",
  "d": {
    "user_id": "123456789012345678",
    "username": "johndoe",
    "status": "friend",
    "presence": {
      "status": "online"
    },
    "created_at": 1704067200
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| user_id | string | ID of the other user in the relationship |
| username | string? | Username of the other user |
| status | string | Relationship status: `pending_incoming`, `pending_outgoing`, `friend`, `blocked` |
| presence | object? | Presence info (for friends) |
| message | string? | Friend request message (for pending requests) |
| created_at | int? | Timestamp when relationship was created |

### RELATIONSHIP_REMOVE

Sent when a relationship is removed. This includes:
- Friend removed
- Friend request declined or cancelled
- User unblocked
- Blocked by another user (friendship removed)

```json
{
  "t": "RELATIONSHIP_REMOVE",
  "d": {
    "user_id": "123456789012345678"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| user_id | string | ID of the user whose relationship was removed |
