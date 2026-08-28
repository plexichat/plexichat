# User Features API

Endpoints for managing user features, badges, and rate limit tiers.

## Public Endpoints

### GET /users/@me/features

Get current user's features and badges.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
{
  "badges": [
    {
      "name": "alpha_tester",
      "display_name": "Alpha Tester",
      "description": "Early alpha tester",
      "icon": ":test_tube:",
      "color": "#9b59b6"
    }
  ],
  "tier": "alpha",
  "tier_limits": {
    "multiplier": 2.0,
    "max_voice_minutes_per_day": 480,
    "max_file_uploads_per_day": 200,
    "max_file_size_mb": 25
  }
}
```

### Response Fields

- `badges` (array): User's badges with display info
- `tier` (string): User's rate limit tier
- `tier_limits` (object): Tier-specific limits

---

## Admin Endpoints

These endpoints require administrator permission.

### GET /admin/users/{user_id}/features

Get features for a specific user.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
{
  "user_id": "123456789012345678",
  "rate_limit_tier": "alpha",
  "badges": ["alpha_tester", "early_supporter"],
  "tier_limits": {
    "multiplier": 2.0,
    "max_voice_minutes_per_day": 480,
    "max_file_uploads_per_day": 200,
    "max_file_size_mb": 25
  },
  "expires_at": null
}
```

### PUT /admin/users/{user_id}/features

Update features for a specific user.

### Headers

```
Authorization: Bearer <token>
```

### Request Body

- `rate_limit_tier` (string, optional): Rate limit tier
- `expires_at` (int, optional): Unix timestamp when features expire
- `notes` (string, optional): Admin notes

### Example Request

```json
{
  "rate_limit_tier": "premium"
}
```

### Response (200 OK)

Returns the updated user features object.

### PUT /admin/users/{user_id}/tier

Set rate limit tier for a user.

### Headers

```
Authorization: Bearer <token>
```

### Request Body

- `tier` (string, required): Tier name
- `expires_at` (int, optional): Unix timestamp when tier expires

### Example Request

```json
{
  "tier": "premium",
  "expires_at": 1735689600
}
```

### Response (200 OK)

Returns the updated user features object.

### POST /admin/users/{user_id}/badges/{badge}

Add a badge to a user.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
{
  "success": true,
  "badges": ["alpha_tester", "early_supporter", "staff"]
}
```

### Error Responses

- 400 Invalid badge: Badge name not recognized
- 404 User not found: User doesn't exist

### DELETE /admin/users/{user_id}/badges/{badge}

Remove a badge from a user.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
{
  "success": true,
  "badges": ["alpha_tester", "early_supporter"]
}
```

### GET /admin/tiers

Get all available rate limit tiers.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
{
  "tiers": {
    "standard": {
      "multiplier": 1.0,
      "max_voice_minutes_per_day": 120,
      "max_file_uploads_per_day": 50,
      "max_file_size_mb": 10
    },
    "alpha": {
      "multiplier": 2.0,
      "max_voice_minutes_per_day": 480,
      "max_file_uploads_per_day": 200,
      "max_file_size_mb": 25
    },
    "premium": {
      "multiplier": 3.0,
      "max_voice_minutes_per_day": -1,
      "max_file_uploads_per_day": 500,
      "max_file_size_mb": 100
    }
  },
  "default": "standard"
}
```

### GET /admin/badges

Get all available badges.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
{
  "badges": [
    {
      "name": "alpha_tester",
      "display_name": "Alpha Tester",
      "description": "Early alpha tester",
      "icon": ":test_tube:",
      "color": "#9b59b6"
    },
    {
      "name": "early_supporter",
      "display_name": "Early Supporter",
      "description": "Supported the project early",
      "icon": ":gem:",
      "color": "#3498db"
    },
    {
      "name": "staff",
      "display_name": "Staff",
      "description": "Plexichat staff member",
      "icon": ":gear:",
      "color": "#e74c3c"
    },
    {
      "name": "verified",
      "display_name": "Verified",
      "description": "Verified account",
      "icon": ":check:",
      "color": "#2ecc71"
    }
  ]
}
```


---

## Rate Limit Tiers

- standard: 1.0x
- alpha: 2.0x
- premium: 3.0x

The multiplier affects rate limit windows - higher multipliers allow more requests.

---

## Available Badges

- `alpha_tester` (Alpha Tester): Early alpha tester
- `early_supporter` (Early Supporter): Supported the project early
- `staff` (Staff): Plexichat staff member
- `verified` (Verified): Verified account

---

## Message Tools (Feature Expansion)

These endpoints are mounted under `/features` by `src/api/routes/features/__init__.py`.

### POST /features/bookmarks

Bookmark a message.

### Headers

```
Authorization: Bearer <token>
```

### Request Body

- `message_id` (string, required): ID of the message to bookmark
- `conversation_id` (string, required): ID of the conversation
- `label` (string, optional, Max 100 chars): Optional label

### Response (200 OK)

```json
{
  "success": true,
  "bookmark": { "id": "123456789012345678" }
}
```

### Error Responses

- 400 Invalid bookmark request
- 401 Not authenticated

### DELETE /features/bookmarks/{message_id}

Remove a bookmark. Returns `{"success": true}`.

### GET /features/bookmarks

List your bookmarks.

### Query Parameters

- `conversation_id` (string, optional): Filter by conversation
- `limit` (int, optional, 1-100, default 50): Max results

### Response (200 OK)

```json
{
  "bookmarks": [ { "id": "...", "message_id": "...", "label": null, "created_at": 1704067200 } ]
}
```

### POST /features/forward

Forward a message to another conversation you can post in.

### Request Body

- `message_id` (string, required): ID of the message to forward
- `target_conversation_id` (string, required): Target conversation ID

### Response (200 OK)

Returns the created forwarded-message record.

### POST /features/scheduled-messages

Schedule a message for future delivery.

### Request Body

- `conversation_id` (string, required): Target conversation ID
- `content` (string, required, 1-4000 chars): Message content
- `scheduled_at` (int, required): Timestamp (ms) when to send

### Response (200 OK)

```json
{
  "success": true,
  "scheduled_message": { "id": "...", "scheduled_at": 1735689600000 }
}
```

### GET /features/scheduled-messages

List your scheduled messages. Query parameters: `conversation_id` (optional), `status` (optional), `limit` (optional).

### DELETE /features/scheduled-messages/{scheduled_id}

Cancel a pending scheduled message. Returns `{"success": true}`.

---

## Related Endpoints

- [Users](users.md) - User profile management
- [Settings](settings.md) - User preferences
- [Rate Limits](../rate-limits.md) - Rate limiting details
