# Channels routes

This directory defines the HTTP endpoints for channels and channel invites.

## Overview

The `channels` package provides channel lifecycle, invite management, attachment upload, webhook listing, and optional encrypted ratchet state. It uses a mixin-based architecture. Each mixin registers its routes with `add_api_route`, and `ChannelsComposer` combines the mixins into a single `channels_router`.

All handlers delegate to the core servers and messaging modules through `src.api.get_servers()` and dispatch gateway events where appropriate.

## File structure

| File | Description |
|------|-------------|
| `base.py` | `ChannelBase` — shared `APIRouter` and the `_channel_to_response` helper. |
| `crud.py` | `ChannelCRUDMixin` — `GET /channels/{channel_id}`, `PATCH /channels/{channel_id}`, `DELETE /channels/{channel_id}`. |
| `invites.py` | `ChannelInvitesMixin` — create, inspect, join, and delete invites. |
| `webhooks.py` | `ChannelWebhooksMixin` — `GET /channels/{channel_id}/webhooks`. |
| `attachments.py` | `ChannelAttachmentsMixin` — `POST /channels/{channel_id}/attachments`. |
| `ratchet.py` | `register_ratchet_routes` — `GET /channels/{channel_id}/ratchet`, gated by the `channel_ratchet_encryption` license feature. |
| `composer.py` | `ChannelsComposer` — combines all mixins and exports `channels_router`. |
| `helpers.py` | Helpers such as `_channel_to_response` and `_get_upload_limit`. |

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/channels/{channel_id}` | Get a channel. |
| `PATCH` | `/channels/{channel_id}` | Update a channel. |
| `DELETE` | `/channels/{channel_id}` | Delete a channel. |
| `GET` | `/channels/{channel_id}/webhooks` | List webhooks for a channel. |
| `POST` | `/channels/{channel_id}/invites` | Create an invite for a channel. |
| `POST` | `/channels/{channel_id}/attachments` | Upload an attachment to a channel. |
| `GET` | `/invites/{invite_code}` | Get invite metadata. |
| `POST` | `/invites/{invite_code}` | Join a server through an invite. |
| `DELETE` | `/invites/{invite_code}` | Delete an invite. |
| `GET` | `/channels/{channel_id}/ratchet` | Get ratchet state (requires license feature). |

## Examples

### Create an invite

```http
POST /channels/123/invites
Content-Type: application/json
Authorization: Bearer <token>

{
  "max_age": 86400,
  "max_uses": 10,
  "temporary": false
}
```

```json
{
  "code": "abc123",
  "channel_id": "123",
  "server_id": "456",
  "max_age": 86400,
  "max_uses": 10,
  "uses": 0
}
```

### Join through an invite

```http
POST /invites/abc123
Authorization: Bearer <token>
```

## Error handling

Handlers use `raise_mapped` and `map_invite_join_error` from `src.api.routes.exception_mapping` to translate core exceptions:

| Status | Cause |
|--------|-------|
| `400` | Invalid channel ID or invite parameters. |
| `401` | Missing or invalid authentication. |
| `403` | Channel access denied or banned from the server. |
| `404` | Channel or invite not found or expired. |
| `409` | Already a member of the server. |
| `500` | Servers module unavailable or unexpected error. |

All error responses use the standard shape:

```json
{ "detail": { "error": { "code": 404, "message": "Invite not found or expired" } } }
```

## Dependencies

* Core servers module (`src.api.get_servers()`) for channels, invites, and membership.
* Messaging module for attachment handling.
* Events modules (`src.core.events`, `src.core.events.gateway_emit`) for `INVITE_CREATE`, `INVITE_DELETE`, and `GUILD_CREATE` dispatch.
* Schemas in `src.api.schemas.channels` and `src.api.schemas.common`.
* Authentication middleware (`get_current_user`) for resolving `TokenInfo`.
* Direct-message channel resolution uses user-lookup helpers.

## Notes

* `ChannelsComposer` constructs a fresh `APIRouter(tags=["Channels"])` and calls each mixin's `_register_routes` before registering ratchet routes. The exported `channels_router` is the router you include in the main app.
* Invite creation performs a dual probe (`channel_exists` then `get_channel`) to distinguish `404` (channel not found) from `403` (channel exists but access denied).
* Joining a server dispatches `GUILD_CREATE` and `GUILD_MEMBER_ADD` events asynchronously; failures in event dispatch are logged but do not fail the request.
* Ratchet routes are only registered when the `channel_ratchet_encryption` feature is licensed.
