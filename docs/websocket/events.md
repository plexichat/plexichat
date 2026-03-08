# Gateway Events

Dispatch events are sent with `op: 0` (`DISPATCH`).

## Dispatch Shape

```json
{"op":0,"t":"EVENT_NAME","s":1,"d":{}}
```

| Field | Meaning |
|------|---------|
| `op` | always `0` for dispatch |
| `t` | event type |
| `s` | sequence number |
| `d` | event payload |

## Event Types Defined In Code

The backend currently defines gateway event types including:

### Session and lifecycle

- `READY`
- `RESUMED`
- `SECURITY_LOGOUT`

### Messages and reactions

- `MESSAGE_CREATE`
- `MESSAGE_UPDATE`
- `MESSAGE_DELETE`
- `MESSAGE_DELETE_BULK`
- `MESSAGE_ACK`
- `MESSAGE_REACTION_ADD`
- `MESSAGE_REACTION_REMOVE`
- `MESSAGE_REACTION_REMOVE_ALL`

### Presence and typing

- `PRESENCE_UPDATE`
- `TYPING_START`
- `TYPING_STOP`
- `USER_UPDATE`

### Channels and servers

- `CHANNEL_CREATE`
- `CHANNEL_UPDATE`
- `CHANNEL_DELETE`
- `CHANNEL_PINS_UPDATE`
- `GUILD_CREATE`
- `GUILD_UPDATE`
- `GUILD_DELETE`
- `GUILD_BAN_ADD`
- `GUILD_BAN_REMOVE`
- `GUILD_EMOJIS_UPDATE`
- `GUILD_ROLE_CREATE`
- `GUILD_ROLE_UPDATE`
- `GUILD_ROLE_DELETE`

### Membership and relationships

- `GUILD_MEMBER_ADD`
- `GUILD_MEMBER_REMOVE`
- `GUILD_MEMBER_UPDATE`
- `GUILD_MEMBERS_CHUNK`
- `RELATIONSHIP_ADD`
- `RELATIONSHIP_REMOVE`

### Voice, invites, and threads

- `VOICE_STATE_UPDATE`
- `VOICE_SERVER_UPDATE`
- `INVITE_CREATE`
- `INVITE_DELETE`
- `THREAD_CREATE`
- `THREAD_UPDATE`
- `THREAD_DELETE`
- `THREAD_MEMBER_UPDATE`

### Notifications

- `NOTIFICATION_CREATE`
- `NOTIFICATION_UPDATE`
- `NOTIFICATION_DELETE`

## Client Guidance

- treat event payloads as route- and feature-specific domain objects
- persist the latest sequence number if you want to support resume
- subscribe to generated OpenAPI and route docs for the underlying resource schemas
- do not assume every event is available to every client; authorization and intents still matter
