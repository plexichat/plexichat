# Data types

Common data types used in the Plexichat REST and gateway APIs.

## Snowflake ID

Every ID in Plexichat is a Snowflake: a 64-bit integer serialized as a string in JSON so that JavaScript clients do not lose precision.

```json
{ "id": "123456789012345678" }
```

### Layout

Plexichat uses a Twitter-style Snowflake. The format is defined in `plexichat/src/utils/encryption/core/snowflake.py`.

```
 63  62                  22 21     17 16     12 11          0
+----+---------------------+---------+---------+-------------+
| 0  | 41-bit timestamp    | 5-bit   | 5-bit   | 12-bit      |
|    | (ms since epoch)    | datacenter | worker | sequence  |
+----+---------------------+---------+---------+-------------+
```

| Bits | Field | Description |
| --- | --- | --- |
| 63 | Unused | Always `0`. Keeps the value positive in signed 64-bit systems. |
| 62–22 | Timestamp | Milliseconds since the Plexichat epoch. 41 bits. |
| 21–17 | Datacenter ID | Datacenter identifier. 5 bits, `0`–`31`. |
| 16–12 | Worker ID | Worker or process identifier. 5 bits, `0`–`31`. |
| 11–0 | Sequence | Per-millisecond sequence number. 12 bits, `0`–`4095`. |

### Epoch

Plexichat uses `2024-01-01T00:00:00Z` as the epoch, which is `1704067200000` milliseconds since the Unix epoch.

### Worker and datacenter IDs

You do not need to configure these values for a single-machine deployment. The generator derives them automatically:

1.  It checks `PLEXICHAT_WORKER_ID` and `PLEXICHAT_DATACENTER_ID` (each `0`–`31`, or any integer taken modulo 32).
2.  If the variables are not set, it hashes the hostname and mixes in the process ID so that two processes on the same host still get different worker IDs.

For distributed or multi-worker deployments, set `PLEXICHAT_WORKER_ID` and `PLEXICHAT_DATACENTER_ID` explicitly for each worker so that IDs remain unique across hosts.

### Extracting the timestamp

Use the epoch and a right-shift of 22 bits.

```python
EPOCH = 1704067200000  # 2024-01-01T00:00:00Z in ms

def snowflake_to_timestamp_ms(snowflake_id: str) -> int:
    return (int(snowflake_id) >> 22) + EPOCH

def snowflake_to_unix_seconds(snowflake_id: str) -> float:
    return snowflake_to_timestamp_ms(snowflake_id) / 1000
```

```javascript
const EPOCH = 1704067200000n;

function snowflakeToTimestampMs(snowflakeId) {
  return (BigInt(snowflakeId) >> 22n) + EPOCH;
}

function snowflakeToUnixSeconds(snowflakeId) {
  return Number(snowflakeToTimestampMs(snowflakeId)) / 1000;
}
```

### Parsing all fields

The generator in `snowflake.py:parse` decodes an ID as:

```python
{
  "timestamp": (snowflake_id >> 22) + epoch,      # ms since Unix epoch
  "datacenter_id": (snowflake_id >> 17) & 0x1F,
  "worker_id": (snowflake_id >> 12) & 0x1F,
  "sequence": snowflake_id & 0xFFF,
}
```

## Timestamps

Plexichat uses one timestamp encoding: Unix time in **milliseconds**. The
REST API and the gateway live events carry the same integer.

| Context | Encoding | Example |
| --- | --- | --- |
| REST API (`created_at`, `edited_at` in most JSON responses) | Unix time in **milliseconds** (integer) | `1704067200000` |
| Gateway live events (`created_at`, `edited_at` in `MESSAGE_CREATE` etc.) | Unix time in **milliseconds** (integer) | `1705314600000` |

The Snowflake timestamp is also in milliseconds since the Plexichat epoch (so milliseconds since the Unix epoch after you add `1704067200000`).

### Converting timestamps

```python
from datetime import datetime, timezone

# Unix milliseconds → datetime
dt = datetime.fromtimestamp(1704067200000 / 1000, tz=timezone.utc)

# datetime → Unix milliseconds
timestamp = int(dt.timestamp() * 1000)
```

```javascript
// Unix milliseconds → Date
const date = new Date(1705314600000);

// Date → Unix milliseconds
const timestamp = date.getTime();
```

The same conversion applies to gateway events.

## Nullable fields

Optional fields can be `null` or omitted. In field tables, nullable fields are marked with `?`.

```json
{
  "avatar_url": null,
  "description": null
}
```

- `avatar_url` (`string?`): Avatar URL, or `null` if not set.

## Pagination

List endpoints use cursor-based pagination with Snowflake IDs.

### Query parameters

| Parameter | Type | Description |
| --- | --- | --- |
| `limit` | `int` | Number of items to return (`1`–`100`). |
| `before` | `string` | Return items with an ID less than this Snowflake. |
| `after` | `string` | Return items with an ID greater than this Snowflake. |

```
GET /channels/123/messages?limit=50&before=234567890123456789
```

### Pagination strategy

Fetch pages until the server returns an empty array. Use the last item's `id` as the next `before` cursor.

```python
def fetch_all_messages(channel_id):
    messages = []
    before = None

    while True:
        params = {"limit": 100}
        if before:
            params["before"] = before

        batch = api.get_messages(channel_id, **params)
        if not batch:
            break

        messages.extend(batch)
        before = batch[-1]["id"]

    return messages
```

```javascript
async function fetchAllMessages(channelId) {
  const messages = [];
  let before = null;

  while (true) {
    const params = { limit: 100 };
    if (before) params.before = before;

    const batch = await api.getMessages(channelId, params);
    if (!batch.length) break;

    messages.push(...batch);
    before = batch[batch.length - 1].id;
  }

  return messages;
}
```

## Version string

Plexichat uses a stage-based version scheme:

```
[stage].[major].[minor]-[build]
```

| Component | Values | Description |
| --- | --- | --- |
| `stage` | `a`, `b`, `c`, `r` | Alpha, Beta, Candidate, Release. |
| `major` | `1`+ | Breaking changes. |
| `minor` | `0`+ | New features. |
| `build` | `1`+ | Build number within the stage. |

Examples: `a.1.0-1` (Alpha 1.0, build 1), `b.2.3-15` (Beta 2.3, build 15), `r.1.0-1` (Release 1.0, build 1).

Versions sort by `stage`, then `major`, `minor`, then `build`:

```python
# a.1.0-1 < a.1.0-2 < a.1.1-1 < a.2.0-1 < b.1.0-1 < r.1.0-1
```

## Boolean values

Booleans are JSON booleans:

```json
{ "nsfw": false, "pinned": true }
```

## Arrays

```json
{
  "attachments": [],
  "embeds": [],
  "roles": ["123456789012345678", "234567890123456789"]
}
```

## Objects

```json
{
  "user": {
    "id": "123456789012345678",
    "username": "johndoe"
  }
}
```

## Common object types

### User object

```json
{
  "id": "123456789012345678",
  "username": "johndoe",
  "avatar_url": "https://...",
  "created_at": 1704067200000
}
```

`created_at` is Unix time in milliseconds. For the precise creation time, decode the Snowflake ID.

### Server object

```json
{
  "id": "123456789012345678",
  "name": "My Server",
  "description": "A cool server",
  "icon_url": "https://...",
  "owner_id": "123456789012345678",
  "member_count": 150,
  "created_at": 1704067200000
}
```

### Channel object

```json
{
  "id": "123456789012345678",
  "server_id": "123456789012345678",
  "name": "general",
  "channel_type": "text",
  "topic": "General discussion",
  "position": 0,
  "nsfw": false,
  "slowmode_seconds": 0,
  "created_at": 1704067200000
}
```

### Message object

```json
{
  "id": "123456789012345678",
  "channel_id": "123456789012345678",
  "author_id": "123456789012345678",
  "content": "Hello, world!",
  "created_at": 1704067200000,
  "edited_at": null,
  "attachments": [],
  "embeds": [],
  "pinned": false
}
```

Gateway live events for messages use the same fields plus `author_username`, `author_avatar_url`, and millisecond timestamps. See the [WebSocket gateway guide](client-development/websocket.md#message-events).

## Presence status values

| Value | Description |
| --- | --- |
| `online` | User is online. |
| `idle` | User is idle or away. |
| `dnd` | Do not disturb. |
| `invisible` | Appear offline. Only valid for the current user. |
| `offline` | User is offline. |

## Relationship status values

| Value | Description |
| --- | --- |
| `friend` | Mutual friendship. |
| `pending_incoming` | Incoming friend request. |
| `pending_outgoing` | Outgoing friend request. |
| `blocked` | User is blocked. |

## Channel types

| Type | Description |
| --- | --- |
| `text` | Text channel for messages. |
| `voice` | Voice channel for audio. |
| `category` | Category for organizing channels. |
| `dm` | Direct message channel. |
| `notes` | Personal notes channel. |
