# REST API Reference

This section organizes the backend REST API by route group. For exact request and response schemas, use the generated OpenAPI docs at `/docs`.

## Base URL

All routes in this section are relative to `https://api.plexichat.com`.

## Public Utility Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | readiness and health signal |
| `GET /version` | current backend version |
| `POST /version/negotiate` | client compatibility negotiation |
| `GET /status` | operational or maintenance status |
| `GET /capabilities` | public runtime constants and feature hints |
| `GET /qr` | local QR code generation |

## Authenticated Product Areas

| Route group | Purpose |
|-------------|---------|
| [Authentication](authentication.md) | registration, login, sessions, 2FA, recovery |
| [Users](users.md) | profiles, notes, presence, self-service user actions |
| [Settings](settings.md) | synced user preference storage |
| [Relationships](relationships.md) | friends, blocks, and relationship state |
| [Servers](servers.md) | servers, members, roles, invites, structure |
| [Channels](channels.md) | channel management and channel-scoped actions |
| [Messages](messages.md) | message CRUD, pins, acks, and attachments |
| [Reactions](reactions.md) | message reactions and reaction user lists |
| [Presence](presence.md) | presence retrieval and updates |
| [Webhooks](webhooks.md) | webhook management and execution |
| [Avatars](avatars.md) | user and server avatar endpoints |
| [Emojis](emojis.md) | custom emoji management |
| [Features](features.md) | public feature visibility plus admin-controlled tiers/badges |

## Additional Route Groups

| Route group | Routes covered |
|-------------|----------------|
| [Search](search.md) | `/search/messages`, `/search/users`, `/search/servers` |
| [Notifications](notifications.md) | current-user notification reads and read markers |
| [Polls](polls.md) | poll creation, voting, results, close, delete |
| [Voice](voice.md) | ICE server discovery and voice channel info |
| [Media](media.md) | hash reporting, resumable upload sessions, compression status |
| [Reports](reports.md) | user and message reporting |
| [Feedback](feedback.md) | feedback submission and status checks |
| [Telemetry](telemetry.md) | response-time and CSP telemetry ingestion |
| [System](system.md) | capabilities, help pages, QR, health, status, version |
| [Admin](admin.md) | operator-only admin authentication, review, security, telemetry, and UI routes |

## Notes

- Some endpoints require normal authentication plus an API access token depending on server policy.
- The public narrative docs intentionally avoid private infrastructure and operator-only procedures.
- Administrative routes are mounted at a configurable root path and are documented separately in [Admin](admin.md).