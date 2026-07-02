# Servers Routes

## Purpose
Exposes server and guild management endpoints, including channels, roles,
members, invites, bans, and automod rule management.

## Module Layout

| File              | Routes                                                                 |
|-------------------|------------------------------------------------------------------------|
| `server_crud.py`  | `GET/POST ""`, `GET/PATCH/DELETE /{server_id}` (server CRUD)           |
| `channels.py`     | `GET/POST /{server_id}/channels`, `GET /{server_id}/invites`           |
| `members.py`      | `GET /{server_id}/members`, `DELETE /{server_id}/members/{member_id}`, `PUT/DELETE /{server_id}/members/{member_id}/roles/{role_id}` |
| `roles.py`        | full CRUD: `GET/POST /{server_id}/roles`, `PATCH/DELETE /{server_id}/roles/{role_id}` |
| `bans.py`         | `GET /{server_id}/bans`, `PUT/DELETE /{server_id}/bans/{user_id}`      |
| `misc.py`         | `POST /{server_id}/leave`, `GET /{server_id}/permissions`              |
| `audit_log.py`    | `GET /{server_id}/audit-logs`                                          |
| `automod.py`      | full CRUD rules + `GET /{server_id}/automod/violations`                |
| `webhooks.py`     | `GET /{server_id}/webhooks`                                            |
| `icon_upload.py`  | `POST /{server_id}/icon`                                               |
| `helpers.py`      | Shared response converter functions (`_server_to_response`, etc.)      |

## Key Responsibilities
- Create, update, and fetch servers
- Manage server channels and categories
- Manage roles, permissions, and member settings
- Handle invites, bans, and audit log access
- Create and manage automod rules and violations

## Main Entry Points
- POST /servers
- PATCH /servers/{server_id}
- GET /servers/{server_id}
- POST /servers/{server_id}/channels
- POST /servers/{server_id}/roles
- POST /servers/{server_id}/invites
- GET /servers/{server_id}/audit-log
- POST /servers/{server_id}/automod/rules

## Usage

```python
# Create a server
POST /servers
{
    "name": "My Server",
    "description": "A cool place",
    "icon": "base64_encoded_image_data"
}

# Create a channel
POST /servers/{server_id}/channels
{
    "name": "general",
    "type": "text",
    "topic": "General discussion"
}

# View audit log
GET /servers/{server_id}/audit-log?limit=50&action=MEMBER_KICK
```

## Error Handling

Routes translate domain exceptions from the `ServerManager` into HTTP responses:

| HTTP Code | Raised For |
|-----------|-----------|
| 400 | `InvalidServerNameError`, `InvalidChannelNameError`, `InvalidRoleNameError`, input validation failures |
| 403 | `ServerAccessDeniedError`, `PermissionDeniedError`, `OwnerCannotLeaveError`, `RoleHierarchyError`, `CannotModifyOwnerError` |
| 404 | `ServerNotFoundError`, `ChannelNotFoundError`, `MemberNotFoundError`, `InviteNotFoundError`, `BanNotFoundError` |
| 409 | `MemberExistsError`, `BanExistsError`, `DefaultRoleError` |
| 429 | `InviteMaxUsesError` (handled as 400) |

```python
try:
    server = await server_manager.create_server(user_id=1, name="My Server")
except InvalidServerNameError:
    raise HTTPException(400, detail="Invalid server name")
except ServerAccessDeniedError:
    raise HTTPException(403, detail="Access denied")
```

## Dependencies
- Core servers, presence, notifications, and automod modules.
- Cached lookups for server and channel data (Redis + in-memory multi-layer cache).
- Schemas for request/response validation.

## Notes
- Uses cache invalidation helpers for server and channel updates.
- Channel responses normalize channel type values for API consumers.
- `__init__.py` is a thin aggregator — all handler logic lives in the per-resource modules above.
- Permission checks are performed at the manager level before any mutation.
