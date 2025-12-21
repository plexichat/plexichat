# User Settings API

Cloud-synced key-value store for user preferences.

Settings are stored server-side and sync across all devices. Use this for storing user preferences like theme, notification settings, and UI customizations.

## GET /settings

Get all settings for the current user.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
{
  "settings": {
    "theme": "dark",
    "notifications_enabled": "true",
    "compact_mode": "false"
  },
  "count": 3,
  "limit": 100
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| settings | object | Key-value pairs of settings |
| count | int | Number of settings stored |
| limit | int | Maximum settings allowed |

## GET /settings/{key}

Get a specific setting by key.

### Headers

```
Authorization: Bearer <token>
```

### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| key | string | Setting key |

### Response (200 OK)

```json
{
  "key": "theme",
  "value": "dark",
  "created_at": 1704067200,
  "updated_at": 1704153600
}
```

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 404 | Setting not found | Setting with key doesn't exist |

## PUT /settings/{key}

Set a setting value. Creates or updates the setting.

### Headers

```
Authorization: Bearer <token>
```

### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| key | string | Setting key |

### Request Body

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| value | string | Yes | Max 10000 chars | Setting value |

### Example Request

```json
{
  "value": "dark"
}
```

### Response (200 OK)

```json
{
  "key": "theme",
  "value": "dark",
  "created_at": 1704067200,
  "updated_at": 1704153600
}
```

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 400 | Limit exceeded | Too many settings stored |
| 400 | Key too long | Key exceeds max length |
| 400 | Value too long | Value exceeds 10000 characters |
| 400 | Key reserved | Key is reserved for system use |

## DELETE /settings/{key}

Delete a setting.

### Headers

```
Authorization: Bearer <token>
```

### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| key | string | Setting key |

### Response (200 OK)

```json
{
  "success": true
}
```

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 404 | Setting not found | Setting with key doesn't exist |

---

## Common Settings Keys

These are commonly used setting keys (not enforced, just conventions):

| Key | Description | Example Values |
|-----|-------------|----------------|
| theme | UI theme | "dark", "light", "system" |
| compact_mode | Compact message display | "true", "false" |
| notifications_enabled | Enable notifications | "true", "false" |
| notification_sound | Notification sound | "default", "none", "custom" |
| message_display | Message grouping | "standard", "compact" |
| developer_mode | Show developer options | "true", "false" |
| locale | Language preference | "en-US", "es-ES" |
| timezone | User timezone | "America/New_York" |

---

## Related Endpoints

- [Users](users.md) - User profile management
- [Features](features.md) - User features and badges
