# REST API reference

This section describes the Plexichat backend REST API by route group. For
exact request and response schemas, see the generated OpenAPI documentation at
`/docs`.

## Base URL

All routes in this section are relative to:

```
https://api.plexichat.com
```

For local development, use `http://localhost:8000`.

The API prefix for most product endpoints is `/api/v1`. Utility endpoints
such as `/health` and `/version` are at the root.

## Authentication

Most endpoints require authentication. Include your session token in the
`Authorization` header:

```
Authorization: Bearer <token>
```

If you receive `401`, your token is missing, invalid, or expired. Some
server policies also require an additional API access token.

## Public utility endpoints

These endpoints do not require authentication unless noted:

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/health` | Readiness and health signal. |
| `GET` | `/version` | Current backend version. |
| `POST` | `/version/negotiate` | Client compatibility negotiation. |
| `GET` | `/status` | Operational or maintenance status. |
| `GET` | `/capabilities` | Public runtime constants and feature hints. |
| `GET` | `/qr` | QR code generation (local). |

## Authenticated product areas

| Area | Document | Description |
| --- | --- | --- |
| Authentication | [Authentication](authentication.md) | Registration, login, sessions, 2FA, and recovery. |
| Users | [Users](users.md) | Profiles, notes, presence, and self-service actions. |
| Settings | [Settings](settings.md) | Synced user preference storage. |
| Relationships | [Relationships](relationships.md) | Friends, blocks, and relationship state. |
| Servers | [Servers](servers.md) | Servers, members, roles, invites, and structure. |
| Channels | [Channels](channels.md) | Channel management and channel-scoped actions. |
| Messages | [Messages](messages.md) | Message CRUD, pins, acknowledgments, and attachments. |
| Reactions | [Reactions](reactions.md) | Message reactions and reaction user lists. |
| Presence | [Presence](presence.md) | Presence retrieval and updates. |
| Webhooks | [Webhooks](webhooks.md) | Webhook management and execution. |
| Avatars | [Avatars](avatars.md) | User and server avatar endpoints. |
| Emojis | [Emojis](emojis.md) | Custom emoji management (`emojis.manage` permission). |
| Soundboard | [Soundboard](soundboard.md) | Per-server sound uploads, permissions, and playback. |
| Features | [Features](features.md) | User tiers (`standard`, `alpha`, `premium`) and badges. |

## Capabilities

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/v1/capabilities` | Per-feature availability state. Requires authentication. Returns `{feature: {state, message, details}}` for client banners. |
| `GET` | `/api/v1/admin/capabilities` | Same as above plus a top-level `summary` with counts and `by_state` grouping. Requires admin privileges. |

## Additional route groups

| Area | Document | Path |
| --- | --- | --- |
| Search | [Search](search.md) | `/search/messages`, `/search/users`, `/search/servers` |
| Notifications | [Notifications](notifications.md) | Notification reads and read markers. |
| Polls | [Polls](polls.md) | Poll creation, voting, results, close, and delete. |
| Voice | [Voice](voice.md) | ICE server discovery and voice channel info. |
| Media | [Media](media.md) | Hash reporting, resumable uploads, and compression status. |
| Reports | [Reports](reports.md) | User and message reporting. |
| Feedback | [Feedback](feedback.md) | Feedback submission and status checks. |
| Telemetry | [Telemetry](telemetry.md) | Response-time and CSP telemetry ingestion. |
| System | [System](system.md) | Capabilities, help pages, QR, health, status, and version. |
| Admin | [Admin](admin.md) | Operator-only admin authentication, review, security, telemetry, and UI routes. |

## Conventions

*   IDs are snowflakes encoded as decimal strings.
*   Timestamps are Unix timestamps. Channel and server timestamps use seconds;
    emoji and feedback timestamps use milliseconds unless noted.
*   All JSON request and response bodies use `application/json` unless an
    endpoint specifies `multipart/form-data`.
*   Errors use the shape `{"error": {"code": <status>, "message": "<text>"}}`.

## Notes

*   Some endpoints require normal authentication plus an API access token
    depending on server policy.
*   The public narrative docs intentionally avoid private infrastructure and
    operator-only procedures.
*   Administrative routes are mounted at a configurable root path (default
    `/admin`) and are documented separately in [Admin](admin.md).
