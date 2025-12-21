# User Features API

Endpoints for managing user features, badges, and rate limit tiers.

## Public Endpoints

### GET /features/users/@me/features

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

| Field | Type | Description |
|-------|------|-------------|
| badges | array | User's badges with display info |
| tier | string | User's rate limit tier |
| tier_limits | object | Tier-specific limits |

---

## Admin Endpoints

These endpoints require administrator permission.

### GET /features/admin/users/{user_id}/features

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

### PUT /features/admin/users/{user_id}/features

Update features for a specific user.

### Headers

```
Authorization: Bearer <token>
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| rate_limit_tier | string | No | Rate limit tier |
| expires_at | int | No | Unix timestamp when features expire |
| notes | string | No | Admin notes |

### Example Request

```json
{
  "rate_limit_tier": "premium"
}
```

### Response (200 OK)

Returns the updated user features object.

### PUT /features/admin/users/{user_id}/tier

Set rate limit tier for a user.

### Headers

```
Authorization: Bearer <token>
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| tier | string | Yes | Tier name |
| expires_at | int | No | Unix timestamp when tier expires |

### Example Request

```json
{
  "tier": "premium",
  "expires_at": 1735689600
}
```

### Response (200 OK)

Returns the updated user features object.

### POST /features/admin/users/{user_id}/badges/{badge}

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

| Status | Code | Description |
|--------|------|-------------|
| 400 | Invalid badge | Badge name not recognized |
| 404 | User not found | User doesn't exist |

### DELETE /features/admin/users/{user_id}/badges/{badge}

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

### GET /features/admin/tiers

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

### GET /features/admin/badges

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
      "description": "PlexiChat staff member",
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

| Tier | Multiplier | Voice Minutes/Day | File Uploads/Day | Max File Size |
|------|------------|-------------------|------------------|---------------|
| standard | 1.0x | 120 | 50 | 10 MB |
| alpha | 2.0x | 480 | 200 | 25 MB |
| premium | 3.0x | Unlimited | 500 | 100 MB |

The multiplier affects rate limit windows - higher multipliers allow more requests.

---

## Available Badges

| Badge | Display Name | Description |
|-------|--------------|-------------|
| alpha_tester | Alpha Tester | Early alpha tester |
| early_supporter | Early Supporter | Supported the project early |
| staff | Staff | PlexiChat staff member |
| verified | Verified | Verified account |

---

## Related Endpoints

- [Users](users.md) - User profile management
- [Settings](settings.md) - User preferences
- [Rate Limits](../rate-limits.md) - Rate limiting details
