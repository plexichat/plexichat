# Servers routes

## Overview

This directory exposes server and guild management endpoints, including channels, roles, members, invites, bans, automod rules, webhooks, and audit logs. You mount the aggregated `router` from `__init__.py`, which includes every per-resource sub-router.

## Module layout

| File | Routes | Purpose |
|------|--------|---------|
| `server_crud.py` | `GET/POST ""`, `GET/PATCH/DELETE /{server_id}` | Server CRUD |
| `channels.py` | `GET/POST /{server_id}/channels`, `GET /{server_id}/invites` | Channel create and list; enriched invite listing |
| `members.py` | `GET /{server_id}/members`, `DELETE /{server_id}/members/{member_id}`, `PATCH /{server_id}/members/{member_id}`, `PUT/DELETE /{server_id}/members/{member_id}/roles/{role_id}` | Member listing, kick, update, and role assignment |
| `roles.py` | `GET/POST /{server_id}/roles`, `PATCH/DELETE /{server_id}/roles/{role_id}` | Role CRUD; wildcard permissions are rejected (`roles.py:201`) |
| `bans.py` | `GET /{server_id}/bans`, `PUT/DELETE /{server_id}/bans/{user_id}` | Ban list and ban/unban |
| `misc.py` | `POST /{server_id}/leave`, `GET /{server_id}/permissions` | Leave server and permission inspection (scopes defined in `permissions.py:42`) |
| `audit_log.py` | `GET /{server_id}/audit-logs` | Paginated audit log |
| `automod.py` | `GET/POST /{server_id}/automod/rules`, `PATCH/DELETE /{server_id}/automod/rules/{rule_id}`, `GET /{server_id}/automod/violations` | Automod rule CRUD and violation listing |
| `webhooks.py` | `GET /{server_id}/webhooks` | List server webhooks (`webhooks.py:14`) |
| `icon_upload.py` | `POST /{server_id}/icon` | Server icon upload (size limit from `media.size_limits.icon`, default 2 MiB) |
| `helpers.py` | — | Shared converters: `_server_to_response()`, `_channel_to_response()`, `_automod_rule_to_response()` |

`__init__.py:20` is a thin aggregator. All handler logic lives in the per-resource modules listed above.

## Key responsibilities

* Create, update, fetch, and delete servers.
* Manage server channels and categories. The request field is `channel_type`, not `type` (see `ChannelCreateRequest` in `src/api/schemas/servers.py:79` and `channels.py:130`).
* Manage roles, permissions, and member settings. Wildcard permissions (`"*"`) are rejected; use explicit scopes instead.
* Handle invites, bans, and audit log access.
* Create and manage automod rules and violations.
* List webhooks for a server.

## Endpoints

### Servers

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/servers` | Create a server |
| `GET` | `/servers/{server_id}` | Get a server |
| `PATCH` | `/servers/{server_id}` | Update a server |
| `DELETE` | `/servers/{server_id}` | Delete a server |
| `POST` | `/servers/{server_id}/leave` | Leave a server |
| `POST` | `/servers/{server_id}/icon` | Upload a server icon |

### Channels and invites

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/servers/{server_id}/channels` | List server channels (cached `ttl=30`, `prefix="server_channels_api"`) |
| `POST` | `/servers/{server_id}/channels` | Create a channel |
| `GET` | `/servers/{server_id}/invites` | List invites with inviter enrichment |

### Members and roles

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/servers/{server_id}/members?limit=&after=` | List members (`limit` `1–200`, default `100`; `after` is a cursor) |
| `PATCH` | `/servers/{server_id}/members/{member_id}` | Update nickname, mute, deafen, or timeout |
| `DELETE` | `/servers/{server_id}/members/{member_id}` | Kick a member (optional `reason` in body) |
| `PUT` | `/servers/{server_id}/members/{member_id}/roles/{role_id}` | Assign a role |
| `DELETE` | `/servers/{server_id}/members/{member_id}/roles/{role_id}` | Remove a role |
| `GET` | `/servers/{server_id}/roles` | List roles |
| `POST` | `/servers/{server_id}/roles` | Create a role |
| `PATCH` | `/servers/{server_id}/roles/{role_id}` | Update a role |
| `DELETE` | `/servers/{server_id}/roles/{role_id}` | Delete a role |
| `GET` | `/servers/{server_id}/permissions` | Get your permissions for the server |

### Moderation and observability

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/servers/{server_id}/bans` | List bans |
| `PUT` | `/servers/{server_id}/bans/{user_id}` | Ban a user |
| `DELETE` | `/servers/{server_id}/bans/{user_id}` | Unban a user |
| `GET` | `/servers/{server_id}/audit-logs` | List audit log entries |
| `GET` | `/servers/{server_id}/automod/rules` | List automod rules |
| `POST` | `/servers/{server_id}/automod/rules` | Create an automod rule |
| `PATCH` | `/servers/{server_id}/automod/rules/{rule_id}` | Update an automod rule |
| `DELETE` | `/servers/{server_id}/automod/rules/{rule_id}` | Delete an automod rule |
| `GET` | `/servers/{server_id}/automod/violations` | List automod violations |
| `GET` | `/servers/{server_id}/webhooks` | List webhooks |

## Request examples

Create a server:

```python
POST /servers
{
  "name": "My Server",
  "description": "A cool place"
}
```

Create a channel. Use `channel_type`, not `type`:

```python
POST /servers/123/channels
{
  "name": "general",
  "channel_type": "text",
  "topic": "General discussion",
  "category_id": null,
  "nsfw": false,
  "slowmode_seconds": 0,
  "read_receipts_enabled": true
}
```

View audit logs:

```python
GET /servers/123/audit-logs?limit=50
```

## Error handling

Routes translate domain exceptions from `ServerManager` into HTTP responses:

| HTTP code | Raised for |
|-----------|------------|
| `400` | `InvalidServerNameError`, `InvalidChannelNameError`, `InvalidRoleNameError`, invalid `server_id` or `channel_id`, wildcard permission (`roles.py:201`) |
| `403` | `ServerAccessDeniedError`, `PermissionDeniedError`, `OwnerCannotLeaveError`, `RoleHierarchyError`, `CannotModifyOwnerError` |
| `404` | `ServerNotFoundError`, `ChannelNotFoundError`, `MemberNotFoundError`, `InviteNotFoundError`, `BanNotFoundError` |
| `409` | `MemberExistsError`, `BanExistsError`, `DefaultRoleError` |
| `500` | Missing module or unexpected failure; `InviteMaxUsesError` is handled as `400` |

Example:

```python
try:
    server = await server_manager.create_server(user_id=1, name="My Server")
except InvalidServerNameError:
    raise HTTPException(status_code=400, detail="Invalid server name")
except ServerAccessDeniedError:
    raise HTTPException(status_code=403, detail="Access denied")
```

## Channel response shape

`_channel_to_response()` in `helpers.py:35` normalizes the type field:

* Input model field is `channel.channel_type` (enum `ChannelType`).
* Output JSON field is `channel_type` with values `text`, `voice`, or `category`.

## Dependencies

* Core `servers`, `presence`, `notifications`, `automod`, and `webhooks` modules.
* Cached lookups for server and channel data (Redis + in-memory multi-layer cache).
* `src/api/schemas/servers.py` for request and response validation.

## Notes

* Use cache invalidation helpers `invalidate_server_channels()` after channel mutations (`channels.py:151`).
* Permission checks are performed at the manager level before any mutation and use scopes defined in `permissions.py:42`.
* The `GET /{server_id}/members` endpoint fetches `limit + 1` rows to compute `has_more` without a second query (`members.py:76`).
* Icon uploads respect `media.size_limits.icon` from config; the default is `2 * 1024 * 1024` bytes (`helpers.py:10`).
