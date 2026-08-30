# Servers API

Manage servers (guilds), channels, members, roles, bans, invites, auditing, and automation. You use this API to create and configure servers you own or moderate.

All endpoints in this document are relative to `https://api.plexichat.com`.
For local development, use `http://localhost:8000`.

You must authenticate with `Authorization: Bearer <token>` for all server endpoints unless noted.

## Server resource

```json
{
  "id": "123456789012345678",
  "name": "My Server",
  "description": "A cool server",
  "icon_url": "https://cdn.example.com/icons/123.png",
  "owner_id": "123456789012345678",
  "member_count": 150,
  "default_channel_id": "234567890123456789",
  "verification_level": 0,
  "default_message_notifications": 0,
  "max_reactions_per_message": 20,
  "created_at": 1704067200
}
```

### Properties

| Field | Type | Description |
| --- | --- | --- |
| `id` | string | Snowflake ID of the server. |
| `name` | string | Server name, `2`–`100` characters. |
| `description` | string or null | Server description, max `1000` characters. |
| `icon_url` | string or null | Server icon URL. |
| `owner_id` | string | Snowflake ID of the owner. |
| `member_count` | integer | Number of members. |
| `default_channel_id` | string or null | Default channel selected when joining. |
| `verification_level` | integer | Verification level required to join, `0`–`4`. |
| `default_message_notifications` | integer | Default notification level: `0` = all messages, `1` = mentions only. |
| `max_reactions_per_message` | integer | Maximum distinct reactions per message, `1`–`200`, default `20`. |
| `created_at` | integer | Unix timestamp in seconds when the server was created. |

Additional fields that may be set on update:

| Field | Type | Description |
| --- | --- | --- |
| `system_channel_id` | string or null | Channel for system messages. Send `0` to clear. |
| `icon_url` | string or null | Direct icon URL, or upload via the icon route for avatar processing. |

## List your servers

Returns servers you are a member of.

```
GET /api/v1/servers
```

Results are cached for 30 seconds.

### Example request

```bash
curl -X GET https://api.plexichat.com/api/v1/servers \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

### Response

`200 OK` with an array of server resources.

### Errors

| Status | Description |
| --- | --- |
| `401` | Not authenticated. |
| `500` | Servers module not available. |

## Create a server

Create a server. You become the owner.

```
POST /api/v1/servers
```

### Request body

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string | Yes | Server name, `2`–`100` characters. |
| `description` | string | No | Server description, max `1000` characters. |
| `icon_url` | string | No | Initial icon URL. If provided, it is stored as the server icon. |

### Example request

```bash
curl -X POST https://api.plexichat.com/api/v1/servers \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My New Server",
    "description": "A place for friends",
    "icon_url": "https://cdn.example.com/icons/new.png"
  }'
```

### Response

`200 OK` with the created server resource.

### Errors

| Status | Description |
| --- | --- |
| `400` | Server limit reached or invalid data. |
| `401` | Not authenticated. |

## Get a server

Get server details. You must be a member.

```
GET /api/v1/servers/{server_id}
```

Results are cached for 60 seconds.

### Path parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `server_id` | string | Yes | Snowflake ID of the server. |

### Response

`200 OK` with a server resource.

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid server ID format. |
| `403` | You are not a member. |
| `404` | Server does not exist. |

## Update a server

Update server settings. Requires the `server.manage` permission.

```
PATCH /api/v1/servers/{server_id}
```

### Request body

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string | No | Server name, `2`–`100` characters. |
| `description` | string | No | Server description, max `1000` characters. |
| `icon_url` | string | No | Icon URL. |
| `default_channel_id` | string | No | Default channel ID. |
| `system_channel_id` | string | No | System channel ID. Send `0` to clear. |
| `verification_level` | integer | No | `0`–`4`. |
| `default_message_notifications` | integer | No | `0` or `1`. |
| `max_reactions_per_message` | integer | No | `1`–`200`. |

If you update `default_channel_id`, the server channel cache is invalidated.

### Response

`200 OK` with the updated server resource.

### Errors

| Status | Description |
| --- | --- |
| `403` | Missing `server.manage` permission. |
| `404` | Server does not exist. |

## Delete a server

Delete a server. Only the owner can delete a server.

```
DELETE /api/v1/servers/{server_id}
```

### Response

```json
{
  "success": true
}
```

### Errors

| Status | Description |
| --- | --- |
| `403` | You are not the owner. |
| `404` | Server does not exist. |

## Upload a server icon

Upload an image to set the server icon. Requires the `server.manage` permission.

```
POST /api/v1/servers/{server_id}/icon
Content-Type: multipart/form-data
```

The server validates the image type and size, uploads it through the avatars module, emits a `GUILD_UPDATE` event so connected clients refresh the icon, and persists the new `icon_url`.

### Request body

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `file` | file | Yes | Image file. Supported types: `JPEG`, `PNG`, `GIF`, `WebP`. Default size limit `2 MB`. |

### Response

`200 OK` with the updated server resource containing the new `icon_url`.

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid server ID or invalid image file (unsupported type, too large, blocked, or malware detected). |
| `403` | Missing `server.manage` permission. |
| `404` | Server does not exist. |

## Leave a server

Leave a server. Owners cannot leave their own server; transfer ownership first.

```
POST /api/v1/servers/{server_id}/leave
```

### Response

```json
{
  "success": true
}
```

### Errors

| Status | Description |
| --- | --- |
| `403` | Owners cannot leave their own server. |
| `404` | Server does not exist. |

## Get your permissions

Get your current permissions in a server as a map of permission keys to booleans.

```
GET /api/v1/servers/{server_id}/permissions
```

### Response

`200 OK`

```json
{
  "server.view": true,
  "channel.create": false,
  "message.send": true,
  "message.manage": false,
  "server.manage": false
}
```

## Channels

### List server channels

Get channels in a server. You only receive channels you have permission to view.

```
GET /api/v1/servers/{server_id}/channels
```

Results are cached for 30 seconds.

### Response

`200 OK`

```json
[
  {
    "id": "123456789012345678",
    "server_id": "123456789012345678",
    "name": "general",
    "channel_type": "text",
    "topic": "General discussion",
    "position": 0,
    "category_id": null,
    "nsfw": false,
    "slowmode_seconds": 0,
    "read_receipts_enabled": true,
    "created_at": 1704067200
  }
]
```

### Errors

| Status | Description |
| --- | --- |
| `403` | You are not a member. |
| `404` | Server does not exist. |

### Create a channel

Create a channel in a server. Requires the `channel.create` or `channels.manage` permission.

```
POST /api/v1/servers/{server_id}/channels
```

### Request body

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string | Yes | Channel name, `1`–`100` characters. |
| `channel_type` | string | No | Channel type: `text`, `voice`, `announcement`, `stage`, or `category`. Default `text`. The legacy alias `type` is also accepted. |
| `topic` | string | No | Channel topic, max `1024` characters. |
| `category_id` | string | No | Parent category ID. |
| `nsfw` | boolean | No | NSFW flag. |
| `slowmode_seconds` | integer | No | Slowmode delay `0`–`21600`. |
| `read_receipts_enabled` | boolean | No | Whether to track read receipts. |

The API invalidates the server channel cache and broadcasts a `CHANNEL_CREATE` event to all members.

### Response

`200 OK` with the created channel resource.

## Members

### List server members

Get members of a server. Requires membership. Results are paginated.

```
GET /api/v1/servers/{server_id}/members
```

Results are cached for 30 seconds.

### Query parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `limit` | integer | No | Maximum members to return, `1`–`200`, default `100`. The API fetches one extra row to compute `has_more`. |
| `after` | string | No | Snowflake ID. Return members after this user ID. |

### Response

`200 OK`

```json
{
  "items": [
    {
      "user_id": "123456789012345678",
      "username": "johndoe",
      "avatar_url": "https://cdn.example.com/avatars/123.png",
      "nickname": "John",
      "roles": ["234567890123456789"],
      "joined_at": 1704067200,
      "presence": {"status": "online"}
    }
  ],
  "has_more": true
}
```

| Field | Type | Description |
| --- | --- | --- |
| `items` | array | Page of member resources. Each includes `member_id`, `user_id`, `username`, `nickname`, `avatar_url`, `roles`, `joined_at`, `presence`, `badges`, `timeout_until`, and `timeout_reason`. |
| `has_more` | boolean | Whether more members exist after this page. |

Members are enriched with live profile data and bulk presence in a single lookup.

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid server ID or `after` cursor. |
| `403` | You are not a member. |
| `404` | Server does not exist. |

### Update a server member

Update a member's server-specific state, such as nickname, mute, deafen, or timeout.

```
PATCH /api/v1/servers/{server_id}/members/{member_id}
```

`member_id` can be the membership record ID or the user ID. The API looks up both.

### Request body

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `nickname` | string | No | New nickname, max `100` characters. |
| `muted` | boolean | No | Server muting state. |
| `deafened` | boolean | No | Server deafening state. |
| `timeout_until` | integer | No | Timestamp in milliseconds until the timeout ends. |
| `timeout_reason` | string | No | Reason for the timeout. |

### Response

`200 OK` with the updated member resource.

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid ID. |
| `403` | Missing permission or role hierarchy violation. |
| `404` | Member does not exist. |

### Kick a member

Remove a member from a server. Requires the `member.kick` permission.

```
DELETE /api/v1/servers/{server_id}/members/{member_id}
```

`member_id` can be the membership record ID or the user ID.

You can include a JSON body with a `reason` field (max `512` characters) for the audit trail:

```json
{
  "reason": "Spam"
}
```

### Errors

| Status | Description |
| --- | --- |
| `403` | Missing permission or hierarchy violation. |
| `404` | Member does not exist. |

### Assign a role to a member

```
PUT /api/v1/servers/{server_id}/members/{member_id}/roles/{role_id}
```

Requires the `roles.manage` permission. If the member already has the role, the API returns `200`.

### Remove a role from a member

```
DELETE /api/v1/servers/{server_id}/members/{member_id}/roles/{role_id}
```

Requires the `roles.manage` permission.

## Roles

### List server roles

Get all roles in a server.

```
GET /api/v1/servers/{server_id}/roles
```

Results are cached for 300 seconds.

### Response

`200 OK`

```json
[
  {
    "id": "123456789012345678",
    "server_id": "123456789012345678",
    "name": "Admin",
    "color": "#ff0000",
    "position": 1,
    "permissions": {},
    "hoist": true,
    "mentionable": true,
    "is_default": false
  }
]
```

### Create a role

Create a role. Requires the `roles.manage` permission.

```
POST /api/v1/servers/{server_id}/roles
```

### Request body

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string | Yes | Role name, `1`–`100` characters. |
| `color` | string | No | Hex color in `#RRGGBB` format. |
| `permissions` | object | No | Permission flags. |
| `hoist` | boolean | No | Display separately in the member list. |
| `mentionable` | boolean | No | Whether the role can be mentioned. |

The API emits a `GUILD_ROLE_CREATE` event and invalidates the cached role listing.

### Errors

| Status | Description |
| --- | --- |
| `400` | Role limit reached or invalid permission key. |
| `403` | Missing permission. |

### Update a role

Update a role. Requires the `roles.manage` permission.

```
PATCH /api/v1/servers/{server_id}/roles/{role_id}
```

All fields from creation are optional on update. Adds `position` (integer `>= 0`) to reorder roles. The API validates that `color` matches `#RRGGBB`, emits `GUILD_ROLE_UPDATE`, and invalidates the cache.

### Delete a role

Delete a role. Requires the `roles.manage` permission. You cannot delete the default `@everyone` role.

```
DELETE /api/v1/servers/{server_id}/roles/{role_id}
```

The API emits `GUILD_ROLE_DELETE`.

## Bans

### List server bans

Get all bans in a server. Requires the `member.ban` permission. Bans are enriched with the banned user's public profile so clients can render real usernames.

```
GET /api/v1/servers/{server_id}/bans
```

### Response

`200 OK`

```json
[
  {
    "user_id": "123456789012345678",
    "user": {"id": "123456789012345678", "username": "spammer"},
    "reason": "Spam",
    "banned_by": "234567890123456789",
    "banned_at": 1704067200
  }
]
```

### Ban a user

Ban a user from a server. Requires the `member.ban` permission.

```
PUT /api/v1/servers/{server_id}/bans/{user_id}
```

### Request body

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `reason` | string | No | Ban reason, max `512` characters. |
| `delete_message_days` | integer | No | Days of messages to delete, `0`–`7`, default `0`. |

The API emits `GUILD_BAN_ADD`.

### Unban a user

Unban a user. Requires the `member.ban` permission.

```
DELETE /api/v1/servers/{server_id}/bans/{user_id}
```

The API emits `GUILD_BAN_REMOVE`.

## Invites

### List server invites

Get all invites for a server. Requires the `server.manage` permission. Invites are enriched with the inviter's public profile.

```
GET /api/v1/servers/{server_id}/invites
```

### Response

`200 OK`

```json
[
  {
    "code": "abc123",
    "server_id": "123456789012345678",
    "channel_id": "234567890123456789",
    "inviter_id": "345678901234567890",
    "inviter": {"id": "345678901234567890", "username": "alice"},
    "uses": 5,
    "max_uses": 100,
    "max_age": 86400,
    "temporary": false,
    "created_at": 1704067200,
    "expires_at": 1704153600
  }
]
```

## Audit log

### Get audit log entries

Get audit log entries for a server. Requires the `audit_log.view` permission.

```
GET /api/v1/servers/{server_id}/audit-logs
```

### Query parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `limit` | integer | No | Maximum entries to return, default `50`. |

Entries are enriched with the acting user's public profile and the target name (member username, role name, or channel name).

### Response

`200 OK`

```json
[
  {
    "id": "123456789012345678",
    "server_id": "123456789012345678",
    "user_id": "234567890123456789",
    "user": {"id": "234567890123456789", "username": "alice"},
    "action": "MEMBER_KICK",
    "target_type": "member",
    "target_id": "345678901234567890",
    "target_name": "Bob",
    "changes": {},
    "reason": "Spam",
    "created_at": 1704067200
  }
]
```

## Webhooks

### List server webhooks

Get all webhooks in a server. Requires the `webhooks.manage` permission. Webhooks are retrieved through the webhooks module.

```
GET /api/v1/servers/{server_id}/webhooks
```

### Response

`200 OK`

```json
[
  {
    "id": "123456789012345678",
    "channel_id": "234567890123456789",
    "server_id": "123456789012345678",
    "creator_id": "345678901234567890",
    "name": "My Webhook",
    "avatar_url": "https://cdn.example.com/avatars/webhook.png",
    "created_at": 1704067200
  }
]
```

For channel-level webhook management, see [Channels — Webhooks](channels.md#webhooks).

## Automod

Manage automod rules and review violations. Requires the `server.automod` permission. Rules are stored in the `automod` module.

### List automod rules

```
GET /api/v1/servers/{server_id}/automod/rules
```

### Response

`200 OK` with an array of automod rule resources:

```json
[
  {
    "id": "123456789012345678",
    "server_id": "123456789012345678",
    "name": "Block spam",
    "rule_type": "spam",
    "enabled": true,
    "config": {},
    "actions": [{"action_type": "delete_message"}],
    "applied_roles": [],
    "exempt_roles": [],
    "exempt_channels": [],
    "priority": 0,
    "check_all": false,
    "created_at": 1704067200,
    "updated_at": 1704067200,
    "created_by": "123456789012345678"
  }
]
```

### Create an automod rule

```
POST /api/v1/servers/{server_id}/automod/rules
```

### Request body

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string | Yes | Rule name, `1`–`100` characters. |
| `rule_type` | string | Yes | Rule type, such as `spam`, `keyword`, or `mention_spam`. |
| `config` | object | Yes | Rule configuration. |
| `actions` | array | Yes | Array of actions. Each action has `action_type`, optional `duration_seconds`, `reason`, `notify_user`, and `metadata`. |
| `applied_roles` | array of strings | No | Role IDs the rule applies to (empty = everyone). |
| `exempt_roles` | array of strings | No | Exempt role IDs. |
| `exempt_channels` | array of strings | No | Exempt channel IDs. |
| `priority` | integer | No | Rule priority, default `0`. |
| `check_all` | boolean | No | Whether to check subsequent rules after a match, default `false`. |
| `enabled` | boolean | No | Whether the rule is enabled, default `true`. If `false`, the API creates the rule then disables it. |

### Update an automod rule

```
PATCH /api/v1/servers/{server_id}/automod/rules/{rule_id}
```

All fields are optional. Only provided fields are updated. Use `enabled` to toggle the rule.

### Delete an automod rule

```
DELETE /api/v1/servers/{server_id}/automod/rules/{rule_id}
```

### List automod violations

```
GET /api/v1/servers/{server_id}/automod/violations
```

### Query parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `user_id` | string | No | Filter by violator user ID. |
| `limit` | integer | No | Maximum violations, default `50`. |
| `before` | string | No | Snowflake ID. Return violations before this ID. |

### Get an automod violation

```
GET /api/v1/servers/{server_id}/automod/violations/{violation_id}
```

## Related endpoints

*   [Channels](channels.md) — Channel management, invites, webhooks, and attachments.
*   [Messages](messages.md) — Message operations and pinning.
*   [Webhooks](webhooks.md) — Webhook management.
*   [Reactions](reactions.md) — Message reactions, including moderation wipes.
