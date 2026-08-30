# Gateway intents

Gateway intents let you declare which event families you want to receive on a
WebSocket connection. You pass a bitmask in `IDENTIFY` (`op: 2`). The gateway
then filters dispatch events before delivery.

The source of truth is `GatewayIntent` in `src/core/events/types.py:97` and
validation in `src/api/websocket/intents.py:15`.

## How intents work

*   Each intent is one bit (`1<<n`). You combine intents with bitwise OR.
*   The gateway delivers an event only if you set the intent that covers it.
    For the per-event mapping, see `src/core/events/router.py:13` and
    [Gateway events](events.md).
*   Guild messages need `GUILD_MESSAGES`; direct messages need
    `DIRECT_MESSAGES`. Attach `MESSAGE_CONTENT` if you need raw content.
*   Artifacts need `ARTIFACTS` and PlexiSum workbooks need `SUMS` to receive
    real-time fabric events and ops (`60`–`67`).

## Bitmask summary

| Setting | Value | How it is derived |
|---------|-------|-------------------|
| Default intents | `3178237` | `GatewayIntent.default_intents()` in `src/core/events/types.py:137` |
| All intents | `4194303` | `GatewayIntent.all_intents()` in `src/core/events/types.py:124` (`(1<<22)-1`) |
| Privileged intents | `33026` | `GatewayIntent.privileged_intents()` in `src/core/events/types.py:158` |

Privileged intents require approval: `GUILD_MEMBERS` (`1<<1`),
`GUILD_PRESENCES` (`1<<8`), and `MESSAGE_CONTENT` (`1<<15`). The default
set includes `ARTIFACTS` (`1<<20`) and `SUMS` (`1<<21`) so that
collaborative features work without extra configuration.

## Intent catalog

| Bit | Name | In default set | Privileged | Description |
|-----|------|----------------|------------|-------------|
| `1<<0` | `GUILDS` | Yes | No | Guild lifecycle, channels, roles, and threads. Covers `GUILD_CREATE`, `CHANNEL_CREATE`, `THREAD_CREATE`, and related events. |
| `1<<1` | `GUILD_MEMBERS` | No | **Yes** | Member join, leave, and update events and `GUILD_MEMBERS_CHUNK`. |
| `1<<2` | `GUILD_BANS` | Yes | No | Ban add and remove. |
| `1<<3` | `GUILD_EMOJIS` | Yes | No | Emoji and sticker updates. Covers `GUILD_EMOJIS_UPDATE` and `GUILD_STICKERS_UPDATE`. |
| `1<<4` | `GUILD_INTEGRATIONS` | Yes | No | Integration updates. |
| `1<<5` | `GUILD_WEBHOOKS` | Yes | No | Webhook updates. Covers `WEBHOOKS_UPDATE`. |
| `1<<6` | `GUILD_INVITES` | Yes | No | Invite create and delete. |
| `1<<7` | `GUILD_VOICE_STATES` | Yes | No | Voice state and voice server updates. |
| `1<<8` | `GUILD_PRESENCES` | No | **Yes** | Presence updates. |
| `1<<9` | `GUILD_MESSAGES` | Yes | No | Guild message create, update, and delete (including bulk). |
| `1<<10` | `GUILD_MESSAGE_REACTIONS` | Yes | No | Guild reaction add, remove, remove-all, and remove-emoji, and poll updates that route as reactions. |
| `1<<11` | `GUILD_MESSAGE_TYPING` | Yes | No | Typing indicators in guild channels. |
| `1<<12` | `DIRECT_MESSAGES` | Yes | No | Direct and group-DM message events. |
| `1<<13` | `DIRECT_MESSAGE_REACTIONS` | Yes | No | Reaction events in DMs. |
| `1<<14` | `DIRECT_MESSAGE_TYPING` | Yes | No | Typing indicators in DMs. |
| `1<<15` | `MESSAGE_CONTENT` | No | **Yes** | Raw message content inside events. Without it, content may be empty. |
| `1<<16` | `SCHEDULED_EVENTS` | No | No | Scheduled guild events. |
| `1<<17` | `THREADS` | No | No | Additional thread events for large workspaces. |
| `1<<18` | `AUTOMOD` | No | No | Auto-moderation events. |
| `1<<19` | `AUDIT_LOG` | No | No | Audit log events. |
| `1<<20` | `ARTIFACTS` | Yes | No | Artifact collaboration events for whiteboards and live editors. Required for `ARTIFACT_CREATE`, `ARTIFACT_OP`, and opcodes `60`–`63`. |
| `1<<21` | `SUMS` | Yes | No | PlexiSum workbook collaboration events. Required for `WORKBOOK_UPDATE`, `SUM_OPERATION`, `SUM_PRESENCE`, and opcodes `64`–`67`. |

## How to request intents

Pass the combined bitmask as `intents` in `IDENTIFY`:

```json
{
  "op": 2,
  "d": {
    "token": "SESSION_OR_BOT_TOKEN",
    "intents": 3178237
  }
}
```

*   Use the default bitmask (`3178237`) unless you have a clear reason to
    change it.
*   Validate that `intents` is an integer in `[0, 4194303]`. Non-integers and
    negative values are rejected with `INVALID_INTENTS` (`4013`).
*   If you request privileged intents without approval, the gateway closes the
    connection with `DISALLOWED_INTENTS` (`4014`).

## Usage notes

*   Request fewer intents when you do not need them. You reduce event volume
    and client processing.
*   If you omit an intent, matching events are withheld without error. For
    example, without `GUILD_MEMBERS` you never receive `GUILD_MEMBER_ADD`.
*   Intents do not bypass authorization. Even with the intent set, you receive
    an event only if you have access to the underlying resource.

## Related documentation

*   [WebSocket gateway](index.md) — Gateway overview and lifecycle.
*   [Gateway events](events.md) — Event types and their required intents.
*   [Gateway opcodes](opcodes.md) — Signaling frames for identify, heartbeat,
    voice, ratchet, and fabric.
