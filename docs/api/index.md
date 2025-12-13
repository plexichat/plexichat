# REST API Reference

The PlexiChat REST API provides endpoints for managing users, servers, channels, messages, and more.

## Base URL

```
http://localhost:8000/api/v1
```

## Authentication

Most endpoints require authentication via the `Authorization` header:

```http
Authorization: Bearer <session_token>
Authorization: Bot <bot_token>
```

## Endpoint Categories

### Health & Version

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/health` | Health check | No |
| GET | `/version` | Server version info | No |
| POST | `/version/negotiate` | Version compatibility check | No |
| GET | `/status` | Server operational status | No |

### Authentication

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| POST | `/auth/register` | Register new account | No |
| POST | `/auth/login` | Login | No |
| POST | `/auth/2fa` | Complete 2FA challenge | No |
| POST | `/auth/logout` | Logout current session | Yes |
| GET | `/auth/sessions` | List active sessions | Yes |
| DELETE | `/auth/sessions/{id}` | Revoke specific session | Yes |
| POST | `/auth/sessions/revoke-all` | Revoke all sessions | Yes |
| GET | `/auth/2fa/status` | Get 2FA status | Yes |
| POST | `/auth/2fa/enable` | Start 2FA setup | Yes |
| POST | `/auth/2fa/confirm` | Confirm 2FA setup | Yes |
| POST | `/auth/2fa/disable` | Disable 2FA | Yes |
| GET | `/auth/password-requirements` | Get password policy | No |

### Users

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/users/@me` | Get current user | Yes |
| PATCH | `/users/@me` | Update current user | Yes |
| POST | `/users/@me/avatar` | Upload avatar | Yes |
| GET | `/users/@me/channels` | List DM channels | Yes |
| POST | `/users/@me/channels` | Create/get DM channel | Yes |
| GET | `/users/@me/notes` | Get personal notes channel | Yes |
| GET | `/users/@me/features` | Get user features/badges | Yes |
| GET | `/users/search` | Search user by username | Yes |
| GET | `/users/{id}` | Get user by ID | Yes |
| GET | `/users/{id}/presence` | Get user presence | Yes |
| PUT | `/users/@me/presence` | Update presence | Yes |

### User Settings

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/settings` | Get all settings | Yes |
| GET | `/settings/{key}` | Get specific setting | Yes |
| PUT | `/settings/{key}` | Set setting value | Yes |
| DELETE | `/settings/{key}` | Delete setting | Yes |

### Servers

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/servers` | List user's servers | Yes |
| POST | `/servers` | Create server | Yes |
| GET | `/servers/{id}` | Get server | Yes |
| PATCH | `/servers/{id}` | Update server | Yes |
| DELETE | `/servers/{id}` | Delete server | Yes |
| GET | `/servers/{id}/channels` | List server channels | Yes |
| POST | `/servers/{id}/channels` | Create channel | Yes |
| GET | `/servers/{id}/members` | List server members | Yes |
| DELETE | `/servers/{id}/members/{id}` | Kick member | Yes |
| GET | `/servers/{id}/roles` | List roles | Yes |
| POST | `/servers/{id}/roles` | Create role | Yes |
| PATCH | `/servers/{id}/roles/{id}` | Update role | Yes |
| DELETE | `/servers/{id}/roles/{id}` | Delete role | Yes |
| PUT | `/servers/{id}/members/{id}/roles/{id}` | Assign role | Yes |
| DELETE | `/servers/{id}/members/{id}/roles/{id}` | Remove role | Yes |
| GET | `/servers/{id}/invites` | List server invites | Yes |
| GET | `/servers/{id}/webhooks` | List server webhooks | Yes |

### Channels

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/channels/{id}` | Get channel | Yes |
| PATCH | `/channels/{id}` | Update channel | Yes |
| DELETE | `/channels/{id}` | Delete channel | Yes |
| POST | `/channels/{id}/invites` | Create invite | Yes |
| POST | `/channels/{id}/attachments` | Upload attachment | Yes |
| GET | `/channels/{id}/webhooks` | List channel webhooks | Yes |

### Invites

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/channels/invites/{code}` | Get invite info | Yes |
| POST | `/channels/invites/{code}` | Join via invite | Yes |
| DELETE | `/channels/invites/{code}` | Delete invite | Yes |

### Messages

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/channels/{id}/messages` | List messages | Yes |
| POST | `/channels/{id}/messages` | Send message | Yes |
| GET | `/channels/{id}/messages/search` | Search messages | Yes |
| GET | `/channels/{id}/messages/{id}` | Get message | Yes |
| PATCH | `/channels/{id}/messages/{id}` | Edit message | Yes |
| DELETE | `/channels/{id}/messages/{id}` | Delete message | Yes |
| POST | `/channels/{id}/messages/ack` | Mark as read | Yes |
| GET | `/channels/{id}/pins` | Get pinned messages | Yes |
| PUT | `/channels/{id}/pins/{id}` | Pin message | Yes |
| DELETE | `/channels/{id}/pins/{id}` | Unpin message | Yes |

### Reactions

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/channels/{id}/messages/{id}/reactions` | List reactions | Yes |
| GET | `/channels/{id}/messages/{id}/reactions/{emoji}` | List reaction users | Yes |
| PUT | `/channels/{id}/messages/{id}/reactions/{emoji}` | Add reaction | Yes |
| DELETE | `/channels/{id}/messages/{id}/reactions/{emoji}` | Remove reaction | Yes |

### Relationships

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/relationships/@me` | List relationships | Yes |
| POST | `/relationships` | Send friend request | Yes |
| PUT | `/relationships/{id}/accept` | Accept friend request | Yes |
| DELETE | `/relationships/{id}` | Remove relationship | Yes |
| POST | `/relationships/block` | Block user | Yes |

### Webhooks

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| POST | `/webhooks` | Create webhook | Yes |
| GET | `/webhooks/{id}` | Get webhook | Yes |
| DELETE | `/webhooks/{id}` | Delete webhook | Yes |
| POST | `/webhooks/{id}/{token}` | Execute webhook | No* |

*Webhook execution requires valid webhook token instead of user auth.

### Avatars

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/avatars/users/{id}` | Get user avatar | No |
| POST | `/avatars/users/@me` | Upload user avatar | Yes |
| DELETE | `/avatars/users/@me` | Delete user avatar | Yes |
| GET | `/avatars/servers/{id}` | Get server icon | No |
| POST | `/avatars/servers/{id}` | Upload server icon | Yes |
| DELETE | `/avatars/servers/{id}` | Delete server icon | Yes |

### Emojis

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/servers/{id}/emojis` | List server emojis | Yes |
| GET | `/servers/{id}/emojis/counts` | Get emoji counts | Yes |
| GET | `/servers/{id}/emojis/{id}` | Get emoji | Yes |
| POST | `/servers/{id}/emojis` | Create emoji | Yes |
| PATCH | `/servers/{id}/emojis/{id}` | Update emoji | Yes |
| DELETE | `/servers/{id}/emojis/{id}` | Delete emoji | Yes |

### Admin (Requires Admin Permission)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/admin/users/{id}/features` | Get user features |
| PUT | `/admin/users/{id}/features` | Update user features |
| PUT | `/admin/users/{id}/tier` | Set user tier |
| POST | `/admin/users/{id}/badges/{badge}` | Add badge |
| DELETE | `/admin/users/{id}/badges/{badge}` | Remove badge |
| GET | `/admin/tiers` | List available tiers |
| GET | `/admin/badges` | List available badges |

## Detailed Documentation

- [Authentication](authentication.md) - Auth endpoints and flows
- [Users](users.md) - User management
- [Settings](settings.md) - Cloud-synced user preferences
- [Features](features.md) - Badges, tiers, and admin endpoints
- [Servers](servers.md) - Server/guild management
- [Channels](channels.md) - Channel management
- [Messages](messages.md) - Messaging endpoints
- [Reactions](reactions.md) - Message reactions
- [Relationships](relationships.md) - Friends and blocks
- [Presence](presence.md) - User status
- [Webhooks](webhooks.md) - Webhook integration
- [Avatars](avatars.md) - Avatar management
- [Emojis](emojis.md) - Custom emoji
