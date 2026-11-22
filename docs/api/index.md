# REST API reference

This section describes the Plexichat backend REST API by route group. For
exact request and response schemas, see the generated OpenAPI documentation at
`/docs`.

## Base URL

All routes in this section are relative to:

```
https://api.plexichat.com/api/v1
```

For local development, use `http://localhost:8000/api/v1`.

Every product endpoint lives under the `/api/v1` prefix (`api.api_prefix`).
`/health` and `/ready` are the only utility endpoints that are also served at
the application root, so a probe may use either `/health` or `/api/v1/health`.
There is no root `/version`, `/status`, `/capabilities` or `/qr`: those exist
only under `/api/v1`, and a request to the bare root returns `404`.

## Authentication

Most endpoints require a session token in the `Authorization` header. Obtain
one through the [Authentication](authentication.md) flows (login and session
creation):

```
Authorization: Bearer <session-token>
```

`Authorization` also accepts `Bot <bot-token>` for bot credentials. This token
identifies the caller making the request.

### API access token (additional gate)

Some deployments add a second, deployment-wide credential. When
`api.access_token_required` is enabled (advertised as `access_token_required`
by `GET /api/v1/capabilities`), API product endpoints must also carry a
permanent API access token in a separate header:

```
Authorization: Bearer <session-token>
X-API-Access-Token: <api-access-token>
```

`X-API-Access-Token` is distinct from the bearer session token: the session
token identifies the user or bot, while the API access token is a separate
server-generated credential checked before bearer verification. Public
utility routes, avatar routes, and admin routes (which manage their own
sessions) are exempt from this gate. A missing header returns
`401 {"error": {"code": 401, "message": "API access token required"}}`; an
invalid one returns
`401 {"error": {"code": 401, "message": "Invalid API access token"}}`.
Supplying `X-API-Access-Token` does not replace `Authorization` — protected
endpoints still require a valid session or bot token.

API access tokens are created and scoped by administrators through the Admin
API: `POST <admin-base>/security/access-tokens` creates one (the value is
generated server-side), and `POST <admin-base>/security/access-tokens/{token_id}/scopes`
adds IP/CIDR scope rules. The default `<admin-base>` is `/api/v1/admin`, and
these routes require the `admin.security.tokens` permission — see
[Admin](admin.md#security-controls). There is no self-service token-creation
endpoint for ordinary users.

If you receive `401`, check which credential is missing or expired: the bearer
token may be invalid, or the server may require a valid `X-API-Access-Token`.

## Public utility endpoints

These endpoints do not require authentication unless noted:

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/health` (also `/api/v1/health`) | Health signal. |
| `GET` | `/ready` (also `/api/v1/ready`) | Readiness probe; returns once HTTP is bound. |
| `GET` | `/api/v1/version` | Current backend version and minimum supported client version. |
| `POST` | `/api/v1/version/negotiate` | Client compatibility negotiation. |
| `GET` | `/api/v1/status` | Operational status and uptime. |
| `GET` | `/api/v1/capabilities` | Public runtime constants and feature hints. No token required. |
| `GET` | `/api/v1/qr` | QR code generation; requires the parameters the schema documents. |

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
| `GET` | `/api/v1/capabilities` | Per-feature availability state. Public: no token required. Returns `{feature: {state, message, details}}` for client banners. |
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

*   When server policy enables the gate, API product endpoints require a
    session token plus an administrator-issued token in the
    `X-API-Access-Token` header (see [Authentication](#authentication)).
*   The public narrative docs intentionally avoid private infrastructure and
    operator-only procedures.
*   Administrative routes are mounted at a configurable root path (default
    `/admin`) and are documented separately in [Admin](admin.md).
