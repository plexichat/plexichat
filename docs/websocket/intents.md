# Gateway Intents

Gateway intents let clients declare which event families they want to receive on the WebSocket connection.

## Bitmask Summary

Values are derived from `GatewayIntent` in `src/core/events/types.py`.

| Setting | Value |
|---------|-------|
| Default intents bitmask | `3178237` |
| All intents bitmask | `4194303` |
| Privileged intents bitmask | `33026` |

Privileged intents (require approval) are `GUILD_MEMBERS` (`1<<1`),
`GUILD_PRESENCES` (`1<<8`), and `MESSAGE_CONTENT` (`1<<15`). The default set
includes the `ARTIFACTS` (`1<<20`) and `SUMS` (`1<<21`) fabric intents.

## Intent Catalog

| Value | Name | In default set | Privileged | Description |
|-------|------|----------------|------------|-------------|
| `1<<0` | `GUILDS` | yes | no | Server lifecycle events |
| `1<<1` | `GUILD_MEMBERS` | no | **yes** | Member join/leave/update events |
| `1<<2` | `GUILD_BANS` | yes | no | Ban events |
| `1<<3` | `GUILD_EMOJIS` | yes | no | Emoji updates |
| `1<<4` | `GUILD_INTEGRATIONS` | yes | no | Integration updates |
| `1<<5` | `GUILD_WEBHOOKS` | yes | no | Webhook updates |
| `1<<6` | `GUILD_INVITES` | yes | no | Invite events |
| `1<<7` | `GUILD_VOICE_STATES` | yes | no | Voice state updates |
| `1<<8` | `GUILD_PRESENCES` | no | **yes** | Presence updates |
| `1<<9` | `GUILD_MESSAGES` | yes | no | Server message events |
| `1<<10` | `GUILD_MESSAGE_REACTIONS` | yes | no | Reaction events |
| `1<<11` | `GUILD_MESSAGE_TYPING` | yes | no | Typing indicators |
| `1<<12` | `DIRECT_MESSAGES` | yes | no | DM events |
| `1<<13` | `DIRECT_MESSAGE_REACTIONS` | yes | no | DM reaction events |
| `1<<14` | `DIRECT_MESSAGE_TYPING` | yes | no | DM typing indicators |
| `1<<15` | `MESSAGE_CONTENT` | no | **yes** | Message content inside events |
| `1<<16` | `SCHEDULED_EVENTS` | no | no | Scheduled server events |
| `1<<17` | `THREADS` | no | no | Thread events |
| `1<<18` | `AUTOMOD` | no | no | Automod events |
| `1<<19` | `AUDIT_LOG` | no | no | Audit log events |
| `1<<20` | `ARTIFACTS` | yes | no | Artifact real-time fabric updates |
| `1<<21` | `SUMS` | yes | no | PlexiSum workbook fabric updates |

## Usage Notes

- clients typically start with the default intent set unless they have a clear need for more
- privileged intents may require additional approval or policy review
- requesting fewer intents reduces unnecessary event volume and client processing work
- if an intent is omitted, matching events may be withheld from the gateway session

## Related Pages

- [WebSocket Overview](index.md)
- [Events](events.md)
- [Opcodes](opcodes.md)
