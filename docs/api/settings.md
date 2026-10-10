# User Settings API

Store and sync user preferences in a server-side key-value store.

**Base URL**: `https://api.plexichat.com/api/v1`

For development, use `http://localhost:8000/api/v1`.

All endpoints in this document are mounted under `/users/@me/settings`. The full paths are shown below.

Settings sync across devices. Use this API for preferences such as theme, notifications, and UI options. Don't use it for server-managed presence state.

## Limits and constraints

| Constraint | Value | Notes |
|------------|-------|-------|
| Max settings per user | 100 | `SettingsConfig.max_settings_per_user` (`plexichat/src/core/settings/models.py:13`) |
| Max key length | 100 characters | |
| Max value length | 10,000 characters | |
| Key charset | `[A-Za-z0-9_-]` | Case-insensitive. Empty or whitespace-only keys are rejected. |
| Reserved prefixes | `_`, `__internal` | User writes to these prefixes are rejected. See server-managed keys below. |
| Bulk max entries | 50 per request | `max_bulk_entries` (`plexichat/src/core/settings/models.py:27`) |
| Bulk max payload | 100,000 bytes | Sum of `len(key) + len(value)` after serialization (`plexichat/src/api/routes/settings.py:199`) |

Legacy keys that don't match the charset are hidden on reads instead of returning an error. The server counts only readable keys toward your limits.

### Server-managed keys

Keys in the single-underscore namespace are reserved for the server. The only recognized server key is `_focused_channel` (`plexichat/src/core/settings/models.py:23`). You can write it through the dedicated path (`PUT /users/@me/settings/_focused_channel` or through the bulk endpoint), but you can't write arbitrary `_`-prefixed keys and you can't delete them through `DELETE /users/@me/settings/{key}`. When bulk includes a server key, it's handled outside the atomic batch and counted separately.

`_focused_channel` expects a JSON string such as `{"channel_id":"123","server_id":"456"}`. The server parses it, calls `presence.set_focused_channel`, and persists the raw value so `GET /_focused_channel` stays consistent with `PUT`.

## Authentication

All endpoints require `Authorization: Bearer <token>`.

## Get all settings

```
GET /api/v1/users/@me/settings
```

Returns all readable key-value pairs for the current user. The response is cached for 60 seconds and invalidated on writes.

### Response

`200 OK` (`SettingsResponse` in `plexichat/src/api/schemas/settings.py:22`)

```json
{
  "settings": {
    "theme": "dark",
    "notifications_enabled": "true"
  },
  "count": 2,
  "limit": 100
}
```

| Field | Type | Description |
|-------|------|-------------|
| `settings` | object | Map of key to string value. |
| `count` | integer | Number of readable settings returned. |
| `limit` | integer | Max settings allowed for this user. |

### Errors

| Status | Condition |
|--------|-----------|
| `401` | Missing or invalid token. |
| `500` | Settings module unavailable or internal error. |

## Get a setting

```
GET /api/v1/users/@me/settings/{key}
```

Gets one setting with timestamps. Uses a targeted query and a 60-second cache.

### Path parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `key` | string | Setting key. |

### Response

`200 OK` (`SettingResponse`)

```json
{
  "key": "theme",
  "value": "dark",
  "created_at": 1704067200,
  "updated_at": 1704153600
}
```

### Errors

| Status | Condition |
|--------|-----------|
| `401` | Missing or invalid token. |
| `404` | Setting not found or hidden legacy key. |
| `500` | Internal error. |

## Set a setting

```
PUT /api/v1/users/@me/settings/{key}
```

Creates or updates a setting.

### Path parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `key` | string | Must match `[A-Za-z0-9_-]` and not use a reserved prefix. |

### Request body

`SettingValue` (`plexichat/src/api/schemas/settings.py:5`)

| Field | Type | Required | Constraints |
|-------|------|----------|-------------|
| `value` | string | Yes | Max 10,000 characters. |

```json
{
  "value": "dark"
}
```

### Special case: `_focused_channel`

When `key` is `_focused_channel`, the server parses `value` as JSON, updates presence, and persists the value with `set_server_setting`. If parsing fails, the server returns `400 Invalid _focused_channel payload` and doesn't store the value.

### Response

`200 OK` (`SettingResponse`) — the created or updated setting.

### Errors

| Status | Condition |
|--------|-----------|
| `400` | Limit exceeded, key too long, key invalid, value too long, key reserved, or invalid `_focused_channel` payload. The body uses `{"error":{"code":400,"message":"Internal server error"}}` for validation failures to avoid leaking details. |
| `401` | Missing or invalid token. |
| `500` | Internal error. |

On success, the server invalidates the list cache and the per-key cache and clears the `profile_visibility` derived cache when that key changes.

## Delete a setting

```
DELETE /api/v1/users/@me/settings/{key}
```

### Path parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `key` | string | Setting key. |

Keys starting with `_` are rejected with `400` — they're server-managed and can't be deleted through the generic endpoint (`plexichat/src/api/routes/settings.py:501`).

### Response

`200 OK`

```json
{
  "success": true
}
```

### Errors

| Status | Condition |
|--------|-----------|
| `400` | `key` starts with `_` (reserved namespace). |
| `401` | Missing or invalid token. |
| `404` | Setting not found. |
| `500` | Internal error. |

## Bulk update settings

```
PUT /api/v1/users/@me/settings/bulk
```

Updates many settings at once. The batch is capped by entry count and payload size and committed atomically for standard keys: either every standard key in the batch lands or none does. Server-managed keys in the same request are handled separately and still counted toward `updated`.

- Declared in `plexichat/src/api/routes/settings.py:147`.
- Full path is `/api/v1/users/@me/settings/bulk` because the router is mounted with prefix `/users/@me/settings` in `plexichat/src/api/routes/__init__.py:78`.
- Register the route before `/{key}` so `/bulk` isn't captured as a key.

### Request body

A JSON object of key-value pairs. Non-string values are serialized with `json.dumps` so booleans, numbers, null, and objects round-trip correctly (`plexichat/src/api/routes/settings.py:196`).

```json
{
  "theme": "dark",
  "compact_mode": true,
  "locale": "en-US"
}
```

The example above stores `compact_mode` as the string `"true"`.

### Validation and caps

1. If the number of entries exceeds `max_bulk_entries` (50), the server returns `400` before any write.
2. The server serializes values, then sums `len(key) + len(value)` for all entries. If the total exceeds `max_bulk_payload_bytes` (100,000), it returns `400`.
3. Each standard key is validated against the charset, length, reserved prefix, and per-user limit inside a single transaction (`plexichat/src/core/settings/manager.py:313`). If any key fails, the whole batch rolls back and each standard key gets the same failure message in `failures`. Server-managed keys don't participate in that transaction.

### Response

`200 OK` (`BulkSettingsResponse` in `plexichat/src/api/schemas/settings.py:32`)

```json
{
  "success": true,
  "updated": 2,
  "failed": 0,
  "failures": {}
}
```

On a validation failure, you get `success: false` with per-key reasons:

```json
{
  "success": false,
  "updated": 0,
  "failed": 2,
  "failures": {
    "theme": "Key 'theme' uses reserved prefix '_'",
    "bad key!": "Key may only contain letters, digits, hyphens and underscores"
  }
}
```

### Server-managed keys in a bulk request

Keys in `_SERVER_SETTING_KEYS` (currently `_focused_channel`) are routed to their dedicated handler. They are persisted outside the standard batch transaction but invalidated and counted in the same response. Unknown `_`-prefixed keys in a bulk request fail with `Reserved server setting cannot be written through the bulk endpoint`.

### Cache invalidation

A successful bulk call invalidates the list cache and each per-key cache for the keys in the request body. If `_focused_channel` and `profile_visibility` are included, the profile-visibility feature cache is also cleared.

### Errors

| Status | Condition |
|--------|-----------|
| `400` | More than 50 entries (`Bulk update limited to 50 entries per request`) or payload too large (`Bulk update payload exceeds 100000 bytes`). Also returned per-key for validation failures inside `failures` with `success: false`. |
| `401` | Missing or invalid token. |
| `500` | Internal error. The server logs the error and still returns a `BulkSettingsResponse` with per-key `Internal server error` when `set_many` raises unexpectedly. |

## Common setting keys

These keys are conventions, not enforced:

| Key | Description | Example values |
|-----|-------------|----------------|
| `theme` | UI theme | `dark`, `light`, `system` |
| `compact_mode` | Compact message display | `true`, `false` |
| `notifications_enabled` | Enable notifications | `true`, `false` |
| `notification_sound` | Notification sound | `default`, `none`, `custom` |
| `message_display` | Message grouping | `standard`, `compact` |
| `developer_mode` | Show developer options | `true`, `false` |
| `locale` | Language preference | `en-US`, `es-ES` |
| `timezone` | User timezone | `America/New_York` |
| `profile_visibility` | Profile privacy | Triggers feature-cache invalidation on write |

## Related endpoints

- [Users](users.md) — User profile management
- [Features](features.md) — Badges and user features
