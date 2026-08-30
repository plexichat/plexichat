# Gateway events

The gateway delivers real-time updates as `DISPATCH` payloads (`op: 0`). Each
payload wraps a typed event in `t` and `d`. This page lists every event type
defined in `src/core/events/types.py:8` and how you handle it.

## Dispatch envelope

Every dispatch uses the same outer shape:

```json
{"op": 0, "t": "EVENT_NAME", "s": 1, "d": {}}
```

| Field | Type | Description |
|-------|------|-------------|
| `op` | integer | Always `0` for dispatch. |
| `t` | string | Event type, such as `MESSAGE_CREATE`. |
| `s` | integer | Monotonic sequence number. Store it if you support resume. |
| `d` | object | Event payload. Schema varies by event. |

You branch on `t` first, then parse `d`. Treat `d` as a domain object
for that feature area (messages, guilds, artifacts) rather than a generic
bag.

## Event catalog

All event names are the string values of `EventType` in
`src/core/events/types.py:8`. Required intents come from
`src/core/events/router.py:13` and `src/api/websocket/intents.py:102`.

### Session and error

| Event | Description | Required intent |
|-------|-------------|-----------------|
| `READY` | Sent after `IDENTIFY` succeeds. Contains `user`, `guilds`, `session_id`, and `resume_gateway_url`. | None |
| `RESUMED` | Sent after `RESUME` succeeds. Confirms the session continues at the last sequence number. | None |
| `ERROR` | Server-to-client error frame for a denied or failed request. Includes a stable `code` such as `guild_members_denied` and an optional `message`. | None |

### Messages

| Event | Description | Required intent |
|-------|-------------|-----------------|
| `MESSAGE_CREATE` | A new message was created. | `GUILD_MESSAGES` for guild messages, `DIRECT_MESSAGES` for DMs |
| `MESSAGE_UPDATE` | A message was edited. `d` includes `edited_timestamp`. | `GUILD_MESSAGES` or `DIRECT_MESSAGES` |
| `MESSAGE_DELETE` | A single message was deleted. | `GUILD_MESSAGES` or `DIRECT_MESSAGES` |
| `MESSAGE_DELETE_BULK` | Multiple messages were deleted from one channel. `d` includes `channel_id`, `message_ids`, `server_id`, and legacy `guild_id`. | `GUILD_MESSAGES` |
| `MESSAGE_ACK` | A message was acknowledged as read. | None |
| `MESSAGE_BOOKMARK_ADD` | The current user bookmarked a message. | None |
| `MESSAGE_BOOKMARK_REMOVE` | The current user removed a message bookmark. | None |

### Reactions and polls

| Event | Description | Required intent |
|-------|-------------|-----------------|
| `MESSAGE_REACTION_ADD` | A user added a reaction. | `GUILD_MESSAGE_REACTIONS` or `DIRECT_MESSAGE_REACTIONS` |
| `MESSAGE_REACTION_REMOVE` | A user removed one reaction. | `GUILD_MESSAGE_REACTIONS` or `DIRECT_MESSAGE_REACTIONS` |
| `MESSAGE_REACTION_REMOVE_ALL` | All reactions on a message were cleared. | `GUILD_MESSAGE_REACTIONS` |
| `MESSAGE_REACTION_REMOVE_EMOJI` | All reactions of a single emoji were removed by a moderator. | `GUILD_MESSAGE_REACTIONS` or `DIRECT_MESSAGE_REACTIONS` |
| `POLL_UPDATE` | A poll changed (vote, change, remove, or close). `d` carries identifiers only; fetch results with `GET /polls/{id}/results` when you receive the event. | None |

### Presence, typing, and user

| Event | Description | Required intent |
|-------|-------------|-----------------|
| `PRESENCE_UPDATE` | A user's presence or activities changed. | `GUILD_PRESENCES` |
| `TYPING_START` | A user started typing in a channel. | `GUILD_MESSAGE_TYPING` or `DIRECT_MESSAGE_TYPING` |
| `TYPING_STOP` | A user stopped typing. | `GUILD_MESSAGE_TYPING` or `DIRECT_MESSAGE_TYPING` |
| `USER_UPDATE` | The current user's profile changed (for example, avatar or username). | None |
| `SECURITY_LOGOUT` | One of your sessions was invalidated and you must re-authenticate. | None |
| `SECURITY_ALERT` | An account security event occurred, such as password change, 2FA disable, or account lock. Display this promptly. | None |

### Channels and guilds

| Event | Description | Required intent |
|-------|-------------|-----------------|
| `CHANNEL_CREATE` | A channel was created. | `GUILDS` |
| `CHANNEL_UPDATE` | A channel was updated. | `GUILDS` |
| `CHANNEL_DELETE` | A channel was deleted. | `GUILDS` |
| `CHANNEL_PINS_UPDATE` | The pinned-message set of a channel changed. | `GUILDS` |
| `GUILD_CREATE` | You gained access to a guild, or a guild was created. | `GUILDS` |
| `GUILD_UPDATE` | A guild's metadata changed, such as name or icon. | `GUILDS` |
| `GUILD_DELETE` | You lost access to a guild, or a guild was deleted. | `GUILDS` |
| `GUILD_BAN_ADD` | A user was banned from a guild. | `GUILD_BANS` |
| `GUILD_BAN_REMOVE` | A user was unbanned. | `GUILD_BANS` |
| `GUILD_EMOJIS_UPDATE` | Custom emojis for a guild changed. | `GUILD_EMOJIS` |
| `GUILD_STICKERS_UPDATE` | Stickers for a guild changed. | `GUILD_EMOJIS` |
| `GUILD_ROLE_CREATE` | A role was created. | `GUILDS` |
| `GUILD_ROLE_UPDATE` | A role was updated. | `GUILDS` |
| `GUILD_ROLE_DELETE` | A role was deleted. | `GUILDS` |

### Membership

| Event | Description | Required intent |
|-------|-------------|-----------------|
| `GUILD_MEMBER_ADD` | A user joined a guild. | `GUILD_MEMBERS` (privileged) |
| `GUILD_MEMBER_REMOVE` | A user left or was removed from a guild. | `GUILD_MEMBERS` (privileged) |
| `GUILD_MEMBER_UPDATE` | A member's guild profile changed, such as nickname or roles. | `GUILD_MEMBERS` (privileged) |
| `GUILD_MEMBERS_CHUNK` | Response to `REQUEST_GUILD_MEMBERS` (`op: 8`). Contains `members`, `chunk_index`, `chunk_count`, and `not_found`. | `GUILD_MEMBERS` (privileged) |

### Voice, webhooks, invites, and threads

| Event | Description | Required intent |
|-------|-------------|-----------------|
| `VOICE_STATE_UPDATE` | A user's voice state changed (mute, deaf, channel). | `GUILD_VOICE_STATES` |
| `VOICE_SERVER_UPDATE` | Voice server routing information changed. | `GUILD_VOICE_STATES` |
| `WEBHOOKS_UPDATE` | Webhooks for a guild or channel were updated. | `GUILD_WEBHOOKS` |
| `INVITE_CREATE` | An invite was created. | `GUILD_INVITES` |
| `INVITE_DELETE` | An invite was deleted. | `GUILD_INVITES` |
| `THREAD_CREATE` | A thread was created. | `GUILDS` |
| `THREAD_UPDATE` | A thread was updated. | `GUILDS` |
| `THREAD_DELETE` | A thread was deleted. | `GUILDS` |
| `THREAD_MEMBER_UPDATE` | Your thread membership changed (for example, you joined or left). | `GUILDS` |

### Relationships and interactions

| Event | Description | Required intent |
|-------|-------------|-----------------|
| `RELATIONSHIP_ADD` | A relationship was added, such as a friend request or friendship. | None |
| `RELATIONSHIP_REMOVE` | A relationship was removed or rejected. | None |
| `INTERACTION_CREATE` | An application interaction was created. Respond with `INTERACTION_RESPONSE` (`op: 31`). | None |

### Notifications

| Event | Description | Required intent |
|-------|-------------|-----------------|
| `NOTIFICATION_CREATE` | A notification was created for you. | None |
| `NOTIFICATION_UPDATE` | A notification was updated, for example marked read. | None |
| `NOTIFICATION_DELETE` | A notification was deleted. | None |

### Encryption

| Event | Description | Required intent |
|-------|-------------|-----------------|
| `RATCHET_UPDATE` | The encryption ratchet for a channel advanced. Corresponds to opcode `RATCHET_UPDATE` (`op: 50`). Rotate local keys when you receive it. | None |

### Artifacts and workbooks

These events back the real-time fabric opcodes `60`–`67` in
`src/api/websocket/opcodes.py:43`. They require fabric intents.

| Event | Description | Required intent |
|-------|-------------|-----------------|
| `ARTIFACT_CREATE` | A collaborative artifact (whiteboard or live editor) was created. | `ARTIFACTS` |
| `ARTIFACT_UPDATE` | Artifact metadata was updated. | `ARTIFACTS` |
| `ARTIFACT_DELETE` | An artifact was deleted. | `ARTIFACTS` |
| `ARTIFACT_OP` | An incremental operation was applied to an artifact. Pairs with opcodes `ARTIFACT_OP` (`op: 62`) and `ARTIFACT_SYNC` (`op: 63`). | `ARTIFACTS` |
| `WORKBOOK_UPDATE` | A PlexiSum workbook mutated (cells, structure, comments, or formats were updated). | `SUMS` |
| `SUM_OPERATION` | An incremental operation was applied to a workbook. Pairs with opcodes `SUM_OP` (`op: 66`) and `SUM_SYNC` (`op: 67`). | `SUMS` |
| `SUM_PRESENCE` | Workbook collaborator presence changed, such as cursor position or selection. | `SUMS` |

## How intents affect delivery

* You declare the events you want in `IDENTIFY` with the `intents` bitmask. See
  [Gateway intents](intents.md) for the full catalog.
* If you omit an intent, the gateway withholds matching dispatch events.
  For example, without `GUILD_MEMBERS` you do not receive `GUILD_MEMBER_ADD`.
* Guild messages require `GUILD_MESSAGES`; direct messages require
  `DIRECT_MESSAGES`. Attach `MESSAGE_CONTENT` (`1<<15`) if you need raw
  message content.
* Events with no required intent always pass intent filtering but still
  respect routing and authorization.

## Client guidance

* Persist the latest `s` and pass it in `RESUME` (`op: 6`) when you reconnect.
* Treat every ID field in `d` as a string. The server stringifies IDs for
  JSON.
* Handle unknown `t` values gracefully. Ignore events you do not recognize
  instead of disconnecting.
* Subscribe to generated OpenAPI and route documentation for the underlying
  resource schemas for each event payload.
* Do not assume every event is available to every client. Authorization and
  intents determine delivery.

## Related documentation

* [WebSocket gateway](index.md) — Connection lifecycle and payload shape.
* [Gateway opcodes](opcodes.md) — All opcodes, including `50` and `60`–`67`.
* [Gateway intents](intents.md) — Intent bitmask and approval requirements.
* [Gateway close codes](close-codes.md) — Reconnect and resume behavior.
