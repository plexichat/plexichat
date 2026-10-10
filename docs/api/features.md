# Features API

Manage user tiers, badges, and feature-related utilities. You can retrieve
your own tier and badges, and if you are an administrator, manage tiers and
badges for any user.

All endpoints in this document are relative to `https://api.plexichat.com`.
For local development, use `http://localhost:8000`.

You must authenticate with `Authorization: Bearer <token>` for all endpoints.

## Tiers

Plexichat uses three rate limit tiers. The tier determines request multipliers
and per-day limits. The tier name is lowercase: `standard`, `alpha`, or
`premium`.

| Tier | Multiplier | Description |
| --- | --- | --- |
| `standard` | `1.0` | Default tier for new users. |
| `alpha` | `2.0` | Higher limits for alpha testers. |
| `premium` | `3.0` | Highest limits, includes unlimited voice/video. |

Higher multipliers allow more requests per rate limit window.

### Tier limits

Each tier includes the following limits:

| Field | Type | Description |
| --- | --- | --- |
| `name` | string | Tier name. |
| `multiplier` | number | Rate limit multiplier. |
| `max_voice_minutes_per_day` | integer | Max voice minutes per day (`-1` = unlimited). |
| `max_video_minutes_per_day` | integer | Max video minutes per day (`-1` = unlimited). |
| `max_file_uploads_per_day` | integer | Max file uploads per day. |
| `max_file_size_mb` | integer | Max file size in MB. |
| `max_servers` | integer | Max servers you can join. |
| `max_message_length` | integer | Max characters per message. |
| `max_reactions_per_message` | integer | Max reactions per message. |
| `max_pins_per_channel` | integer | Max pinned messages per channel. |
| `custom_emoji_slots` | integer | Custom emoji slots. |

Default values:

*   `standard`: `multiplier: 1.0`, voice 120, video 60, uploads 50, file 10 MB
*   `alpha`: `multiplier: 2.0`, voice 480, video 240, uploads 200, file 25 MB
*   `premium`: `multiplier: 3.0`, voice unlimited, video unlimited, uploads 500, file 100 MB

## Badges

Badges are displayed on user profiles and are managed by administrators.

| Badge | Display name | Description |
| --- | --- | --- |
| `alpha_tester` | Alpha Tester | Participated in Plexichat alpha testing. |
| `early_supporter` | Early Supporter | Supported Plexichat early on. |
| `staff` | Staff | Plexichat team member. |
| `verified` | Verified | Verified account. |
| `bug_hunter` | Bug Hunter | Found and reported bugs. |
| `contributor` | Contributor | Contributed code or documentation. |
| `moderator` | Moderator | Community moderator. |
| `partner` | Partner | Plexichat partner. |

Use `GET /admin/badges` to retrieve the full list with icons and colors.

## Get your features

Returns your badges with display information and your current tier.

```
GET /api/v1/users/@me/features
```

### Response

`200 OK`

```json
{
  "badges": [
    {
      "name": "alpha_tester",
      "display_name": "Alpha Tester",
      "description": "Participated in Plexichat alpha testing",
      "icon": "🧪",
      "color": "#9333ea"
    }
  ],
  "tier": "alpha",
  "tier_limits": {
    "name": "alpha",
    "multiplier": 2.0,
    "max_voice_minutes_per_day": 480,
    "max_video_minutes_per_day": 240,
    "max_file_uploads_per_day": 200,
    "max_file_size_mb": 25,
    "max_servers": 200,
    "max_message_length": 2000,
    "max_reactions_per_message": 20,
    "max_pins_per_channel": 50,
    "custom_emoji_slots": 50
  }
}
```

| Field | Type | Description |
| --- | --- | --- |
| `badges` | array | Your badges with display information. |
| `tier` | string | Your rate limit tier (`standard`, `alpha`, `premium`). |
| `tier_limits` | object | Limits for your tier. |

### Errors

| Status | Description |
| --- | --- |
| `401` | Not authenticated. |
| `500` | Features module not available. |

## Admin endpoints

The following endpoints require administrator privileges. You can authenticate
with either a user token that has `administrator` permission or a dedicated
admin session token.

### Get user features

Returns feature flags, badges, and tier information for a specific user.

```
GET /api/v1/admin/users/{user_id}/features
```

#### Response

`200 OK`

```json
{
  "user_id": "123456789012345678",
  "rate_limit_tier": "alpha",
  "badges": ["alpha_tester", "early_supporter"],
  "tier_limits": {
    "name": "alpha",
    "multiplier": 2.0,
    "max_voice_minutes_per_day": 480,
    "max_video_minutes_per_day": 240,
    "max_file_uploads_per_day": 200,
    "max_file_size_mb": 25,
    "max_servers": 200,
    "max_message_length": 2000,
    "max_reactions_per_message": 20,
    "max_pins_per_channel": 50,
    "custom_emoji_slots": 50
  },
  "expires_at": null
}
```

Note that this endpoint returns `rate_limit_tier`, while the public
`/users/@me/features` endpoint returns `tier`. Both fields contain the same
value.

#### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid user ID. |
| `401` | Not authenticated or missing admin access. |
| `403` | Admin access required. |
| `404` | User does not exist. |

### Update user features

Updates tier and expiration for a specific user.

```
PUT /api/v1/admin/users/{user_id}/features
```

#### Request body

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `rate_limit_tier` | string | No | New tier: `standard`, `alpha`, or `premium`. |
| `expires_at` | integer | No | Unix timestamp in milliseconds when the tier expires. `null` = permanent. |
| `notes` | string | No | Admin notes for the change. |

#### Example request

```json
{
  "rate_limit_tier": "premium"
}
```

#### Response

`200 OK` with the updated `UserFeatures` resource (same shape as Get user
features).

#### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid tier name or user ID. |
| `401` | Not authenticated. |
| `403` | Admin access required. |
| `404` | User does not exist. |

### Get available tiers

Returns all configured rate limit tiers.

```
GET /api/v1/admin/tiers
```

#### Response

`200 OK`

```json
{
  "tiers": {
    "standard": {
      "name": "standard",
      "multiplier": 1.0,
      "max_voice_minutes_per_day": 120,
      "max_video_minutes_per_day": 60,
      "max_file_uploads_per_day": 50,
      "max_file_size_mb": 10,
      "max_servers": 100,
      "max_message_length": 2000,
      "max_reactions_per_message": 20,
      "max_pins_per_channel": 50,
      "custom_emoji_slots": 50
    },
    "alpha": {
      "name": "alpha",
      "multiplier": 2.0,
      "max_voice_minutes_per_day": 480,
      "max_video_minutes_per_day": 240,
      "max_file_uploads_per_day": 200,
      "max_file_size_mb": 25,
      "max_servers": 200,
      "max_message_length": 2000,
      "max_reactions_per_message": 20,
      "max_pins_per_channel": 50,
      "custom_emoji_slots": 50
    },
    "premium": {
      "name": "premium",
      "multiplier": 3.0,
      "max_voice_minutes_per_day": -1,
      "max_video_minutes_per_day": -1,
      "max_file_uploads_per_day": 500,
      "max_file_size_mb": 100,
      "max_servers": 500,
      "max_message_length": 2000,
      "max_reactions_per_message": 20,
      "max_pins_per_channel": 50,
      "custom_emoji_slots": 50
    }
  },
  "default": "standard"
}
```

### Get available badges

Returns all badges that can be assigned.

```
GET /api/v1/admin/badges
```

#### Response

`200 OK`

```json
{
  "badges": [
    {
      "name": "alpha_tester",
      "display_name": "Alpha Tester",
      "description": "Participated in Plexichat alpha testing",
      "icon": "🧪",
      "color": "#9333ea"
    },
    {
      "name": "early_supporter",
      "display_name": "Early Supporter",
      "description": "Supported Plexichat early on",
      "icon": "💎",
      "color": "#06b6d4"
    }
  ]
}
```

## Feature expansion

Additional feature endpoints are mounted under `/api/v1/features`. They provide
bookmarks, message forwarding, and scheduled messages. All require
authentication.

### Bookmarks

#### Create a bookmark

```
POST /api/v1/features/bookmarks
```

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `message_id` | string | Yes | ID of the message to bookmark. |
| `conversation_id` | string | Yes | ID of the conversation. |
| `label` | string | No | Optional label, max 100 characters. |

Response `200 OK`:

```json
{
  "success": true,
  "bookmark": { "id": "123456789012345678" }
}
```

#### List bookmarks

```
GET /api/v1/features/bookmarks
```

| Query parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `conversation_id` | string | No | Filter by conversation. |
| `limit` | integer | No | `1`–`100`, default `50`. |

Response `200 OK`:

```json
{
  "bookmarks": [
    { "id": "...", "message_id": "...", "label": null, "created_at": 1704067200 }
  ]
}
```

#### Delete a bookmark

```
DELETE /api/v1/features/bookmarks/{message_id}
```

Response `200 OK`: `{"success": true}`

### Forward a message

Forwards a message to another conversation where you have permission to post.

```
POST /api/v1/features/forward
```

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `message_id` | string | Yes | ID of the message to forward. |
| `target_conversation_id` | string | Yes | Target conversation ID. |

Response `200 OK` with the created forwarded-message record.

### Scheduled messages

#### Create a scheduled message

```
POST /api/v1/features/scheduled-messages
```

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `conversation_id` | string | Yes | Target conversation ID. |
| `content` | string | Yes | Message content, 1–4000 characters. |
| `scheduled_at` | integer | Yes | Timestamp in milliseconds when the message is sent. |

Response `200 OK`:

```json
{
  "success": true,
  "scheduled_message": { "id": "...", "scheduled_at": 1735689600000 }
}
```

#### List scheduled messages

```
GET /api/v1/features/scheduled-messages
```

| Query parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `conversation_id` | string | No | Filter by conversation. |
| `status` | string | No | Filter by status. |
| `limit` | integer | No | Max results. |

#### Cancel a scheduled message

```
DELETE /api/v1/features/scheduled-messages/{scheduled_id}
```

Response `200 OK`: `{"success": true}`

## Related endpoints

*   [Users](users.md): Manage user profiles.
*   [Settings](settings.md): Manage user preferences.
*   [Rate limits](../rate-limits.md): Details on rate limiting and multipliers.
