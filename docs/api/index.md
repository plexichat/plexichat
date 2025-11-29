# REST API Reference

The PlexiChat REST API provides endpoints for managing users, servers, channels, messages, and more.

## Base URL

```
https://api.example.com/api/v1
```

## Endpoints

### Health & Status

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/version` | Server version info |
| POST | `/version/negotiate` | Version compatibility check |
| GET | `/status` | Server status |

### Authentication

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/auth/register` | Register new account |
| POST | `/auth/login` | Login |
| POST | `/auth/2fa` | Complete 2FA |
| POST | `/auth/logout` | Logout |

### Users

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/users/@me` | Get current user |
| PATCH | `/users/@me` | Update current user |
| GET | `/users/{user_id}` | Get user by ID |

### Servers

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/servers` | List user's servers |
| POST | `/servers` | Create server |
| GET | `/servers/{server_id}` | Get server |
| PATCH | `/servers/{server_id}` | Update server |
| DELETE | `/servers/{server_id}` | Delete server |
| GET | `/servers/{server_id}/channels` | List server channels |

### Channels

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/channels/{channel_id}` | Get channel |
| PATCH | `/channels/{channel_id}` | Update channel |
| DELETE | `/channels/{channel_id}` | Delete channel |

### Messages

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/channels/{channel_id}/messages` | List messages |
| POST | `/channels/{channel_id}/messages` | Send message |
| GET | `/channels/{channel_id}/messages/{message_id}` | Get message |
| PATCH | `/channels/{channel_id}/messages/{message_id}` | Edit message |
| DELETE | `/channels/{channel_id}/messages/{message_id}` | Delete message |

### Reactions

| Method | Endpoint | Description |
|--------|----------|-------------|
| PUT | `/channels/{channel_id}/messages/{message_id}/reactions/{emoji}` | Add reaction |
| DELETE | `/channels/{channel_id}/messages/{message_id}/reactions/{emoji}` | Remove reaction |
| GET | `/channels/{channel_id}/messages/{message_id}/reactions` | List reactions |
| GET | `/channels/{channel_id}/messages/{message_id}/reactions/{emoji}` | List reaction users |

### Relationships

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/relationships/@me` | List relationships |
| POST | `/relationships` | Send friend request |
| PUT | `/relationships/{user_id}/accept` | Accept friend request |
| DELETE | `/relationships/{user_id}` | Remove relationship |
| POST | `/relationships/block` | Block user |

### Presence

| Method | Endpoint | Description |
|--------|----------|-------------|
| PUT | `/users/@me/presence` | Update presence |
| GET | `/users/{user_id}/presence` | Get user presence |

### Webhooks

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/webhooks` | Create webhook |
| GET | `/webhooks/{webhook_id}` | Get webhook |
| DELETE | `/webhooks/{webhook_id}` | Delete webhook |
| POST | `/webhooks/{webhook_id}/{token}` | Execute webhook |

## Detailed Documentation

- [Authentication](authentication.md)
- [Users](users.md)
- [Servers](servers.md)
- [Channels](channels.md)
- [Messages](messages.md)
- [Relationships](relationships.md)
- [Presence](presence.md)
- [Reactions](reactions.md)
- [Webhooks](webhooks.md)
