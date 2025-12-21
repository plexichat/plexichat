# Data Types

Common data types used throughout the PlexiChat API.

## Snowflake ID

All IDs in PlexiChat are snowflake IDs - 64-bit integers represented as strings in JSON.

```json
{
  "id": "123456789012345678"
}
```

### Structure

| Bits | Field | Description |
|------|-------|-------------|
| 63-22 | Timestamp | Milliseconds since epoch |
| 21-17 | Datacenter ID | Datacenter identifier |
| 16-12 | Worker ID | Worker process identifier |
| 11-0 | Sequence | Sequence number |

### Epoch

PlexiChat uses January 1, 2024 00:00:00 UTC as the epoch.

### Extracting Timestamp

```python
EPOCH = 1704067200000  # 2024-01-01 00:00:00 UTC in ms

def snowflake_to_timestamp(snowflake_id):
    timestamp_ms = (int(snowflake_id) >> 22) + EPOCH
    return timestamp_ms / 1000  # Unix timestamp in seconds
```

```javascript
const EPOCH = 1704067200000n;

function snowflakeToTimestamp(snowflakeId) {
    const timestamp = (BigInt(snowflakeId) >> 22n) + EPOCH;
    return Number(timestamp) / 1000;
}
```

## Timestamps

All timestamps are Unix timestamps in seconds (integer).

```json
{
  "created_at": 1704067200,
  "edited_at": 1704067300
}
```

### Converting Timestamps

```python
from datetime import datetime

# Unix timestamp to datetime
dt = datetime.fromtimestamp(1704067200)

# datetime to Unix timestamp
timestamp = int(dt.timestamp())
```

```javascript
// Unix timestamp to Date
const date = new Date(1704067200 * 1000);

// Date to Unix timestamp
const timestamp = Math.floor(date.getTime() / 1000);
```

## Nullable Fields

Optional fields may be `null` or omitted:

```json
{
  "avatar_url": null,
  "description": null
}
```

In documentation, nullable fields are marked with `?`:

| Field | Type | Description |
|-------|------|-------------|
| avatar_url | string? | Avatar URL (nullable) |

## Pagination

List endpoints support cursor-based pagination using snowflake IDs.

### Query Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| limit | int | Maximum items to return (1-100) |
| before | string | Get items with ID less than this |
| after | string | Get items with ID greater than this |

### Example

```
GET /channels/123/messages?limit=50&before=234567890123456789
```

### Pagination Strategy

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

## Version String

PlexiChat uses a stage-based versioning scheme:

```
[stage].[major].[minor]-[build]
```

| Component | Values | Description |
|-----------|--------|-------------|
| stage | a, b, c, r | Alpha, Beta, Candidate, Release |
| major | 1+ | Major version (breaking changes) |
| minor | 0+ | Minor version (new features) |
| build | 1+ | Build number |

### Examples

- `a.1.0-1` - Alpha 1.0, build 1
- `b.2.3-15` - Beta 2.3, build 15
- `r.1.0-1` - Release 1.0, build 1

### Comparison

Versions are compared by: stage -> major -> minor -> build

```python
# a.1.0-1 < a.1.0-2 < a.1.1-1 < a.2.0-1 < b.1.0-1 < r.1.0-1
```

## Boolean Values

Boolean values in JSON:

```json
{
  "nsfw": false,
  "pinned": true
}
```

## Arrays

Arrays are represented as JSON arrays:

```json
{
  "attachments": [],
  "embeds": [],
  "roles": ["123456789012345678", "234567890123456789"]
}
```

## Objects

Nested objects are represented as JSON objects:

```json
{
  "user": {
    "id": "123456789012345678",
    "username": "johndoe"
  }
}
```

## Common Object Types

### User Object

```json
{
  "id": "123456789012345678",
  "username": "johndoe",
  "avatar_url": "https://...",
  "created_at": 1704067200
}
```

### Server Object

```json
{
  "id": "123456789012345678",
  "name": "My Server",
  "description": "A cool server",
  "icon_url": "https://...",
  "owner_id": "123456789012345678",
  "member_count": 150,
  "created_at": 1704067200
}
```

### Channel Object

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
  "created_at": 1704067200
}
```

### Message Object

```json
{
  "id": "123456789012345678",
  "channel_id": "123456789012345678",
  "author_id": "123456789012345678",
  "content": "Hello, world!",
  "created_at": 1704067200,
  "edited_at": null,
  "attachments": [],
  "embeds": [],
  "pinned": false
}
```

## Presence Status Values

| Status | Description |
|--------|-------------|
| online | User is online |
| idle | User is idle/away |
| dnd | Do not disturb |
| invisible | Appear offline (only for self) |
| offline | User is offline |

## Relationship Status Values

| Status | Description |
|--------|-------------|
| friend | Mutual friendship |
| pending_incoming | Incoming friend request |
| pending_outgoing | Outgoing friend request |
| blocked | User is blocked |

## Channel Types

| Type | Description |
|------|-------------|
| text | Text channel for messages |
| voice | Voice channel for audio |
| category | Category for organizing channels |
| dm | Direct message channel |
| notes | Personal notes channel |
