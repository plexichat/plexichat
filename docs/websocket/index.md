# WebSocket gateway

The Plexichat gateway delivers real-time events and accepts client signaling
for heartbeats, identification, presence, typing, voice, encryption ratchets,
and collaborative fabric updates.

Use this page for the overall lifecycle. See [Connection](connection.md),
[Events](events.md), [Intents](intents.md), [Opcodes](opcodes.md), and
[Close codes](close-codes.md) for details.

## Gateway URL

Connect to `{{WEBSOCKET_URL}}` with a WebSocket client. The URL includes the
API version and any gateway parameters that your server deployment requires.

## Lifecycle

You follow these steps for every session:

1.  Open a WebSocket connection to the gateway URL.
2.  Receive `HELLO` (`op: 10`) with the heartbeat interval.
3.  Send `IDENTIFY` (`op: 2`) with your session or bot token and desired
    intents.
4.  Receive `READY` (`op: 0`, `t: READY`) with your `session_id` and
    `resume_gateway_url`.
5.  Send `HEARTBEAT` (`op: 1`) on the interval from `HELLO` and handle
    `HEARTBEAT_ACK` (`op: 11`).
6.  Consume `DISPATCH` (`op: 0`) events for the intents you requested.
7.  Resume with `RESUME` (`op: 6`) or reconnect when the connection drops.

For payload details and timings, see [Gateway connection flow](connection.md).

## Payload shape

Every gateway frame uses the same envelope:

```json
{"op": 0, "d": {}, "s": 1, "t": "EVENT_NAME"}
```

| Field | Description |
|-------|-------------|
| `op` | Opcode that identifies the action. See [Gateway opcodes](opcodes.md). |
| `d` | Event or command data. Schema varies by opcode and event. |
| `s` | Sequence number. Set only on dispatch payloads (`op: 0`). Save it for resume. |
| `t` | Dispatch event type when `op` is `DISPATCH`. For example, `MESSAGE_CREATE`. |

You use sequence numbers to detect gaps. If you disconnect, you pass the last
`s` you received in `RESUME`.

## Opcode families

The gateway defines its opcodes in `src/api/websocket/opcodes.py:8`. You use
these families:

*   **Core lifecycle** — `HELLO` (`10`), `IDENTIFY` (`2`), `HEARTBEAT` (`1`),
    `HEARTBEAT_ACK` (`11`), `RESUME` (`6`), `RECONNECT` (`7`),
    `INVALID_SESSION` (`9`), `REQUEST_GUILD_MEMBERS` (`8`), and `DISPATCH`
    (`0`).
*   **Presence and members** — `PRESENCE_UPDATE` (`3`) and
    `VOICE_STATE_UPDATE` (`4`).
*   **Server status** — `SERVER_STATUS` (`12`) and `VERSION_CHECK` (`13`).
*   **Voice signaling** — `VOICE_CONNECT` (`20`) through `VOICE_QUALITY`
    (`26`).
*   **Interactions** — `INTERACTION_CREATE` (`30`) and
    `INTERACTION_RESPONSE` (`31`).
*   **Typing** — `TYPING_START` (`40`) and `TYPING_STOP` (`41`).
*   **Encryption ratchet** — `RATCHET_UPDATE` (`50`) for channel-level key
    rotations.
*   **Real-time fabric** — Artifact opcodes `ARTIFACT_SUBSCRIBE` (`60`),
    `ARTIFACT_UNSUBSCRIBE` (`61`), `ARTIFACT_OP` (`62`), `ARTIFACT_SYNC`
    (`63`) and PlexiSum workbook opcodes `SUM_SUBSCRIBE` (`64`),
    `SUM_UNSUBSCRIBE` (`65`), `SUM_OP` (`66`), `SUM_SYNC` (`67`).

All values, directions, and purposes are listed in
[Gateway opcodes](opcodes.md).

## Events and intents

*   You receive application data through dispatch events (`op: 0`, `t` set).
    The full catalog is in [Gateway events](events.md). It includes session
    events, message and reaction events, channel and guild events, presence and
    typing, voice, invites and threads, relationships, notifications,
    ratchet updates, and artifact and workbook events.
*   You declare which event families you want with the `intents` bitmask in
    `IDENTIFY`. If you omit an intent, the gateway withholds matching events.
    The bitmask, default set, and privileged intents are in
    [Gateway intents](intents.md). Artifacts need `ARTIFACTS` (`1<<20`);
    PlexiSum workbooks need `SUMS` (`1<<21`).

## Next steps

*   Follow [Gateway connection flow](connection.md) to implement identify,
    heartbeat, and resume.
*   Handle dispatch payloads from [Gateway events](events.md) and filter by
    [Gateway intents](intents.md).
*   Send and receive signaling frames from [Gateway opcodes](opcodes.md).
*   Handle disconnects from [Gateway close codes](close-codes.md).
