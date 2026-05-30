# Server Manager

## Purpose
Implements the core server domain logic: servers, channels, roles, members,
permissions, invites, bans, and audit logging.

## Primary Responsibilities
- Validate and create servers, channels, and roles
- Enforce permission checks for server operations
- Manage membership, invites, and bans
- Maintain audit log entries for administrative actions
- Coordinate sub-handlers for modular server workflows

## Module Layout

```
manager/
├── __init__.py       # Thin re-export: from .base import ServerManager
├── base.py           # ServerManager class (init, handlers, delegating methods)
├── protocol.py       # ServerProtocol: base interface declaring cross-mixin attributes
├── server_ops.py     # ServerOpsMixin: server CRUD (create, get, update, delete, transfer ownership)
├── channel_ops.py    # ChannelOpsMixin: channel messaging (send/read messages, slowmode)
├── audit_ops.py      # AuditOpsMixin: audit log retrieval
├── cache_ops.py      # CacheOpsMixin: cache helpers and validation helpers
├── member_ops.py     # MemberOpsMixin: member iteration and removal operations
├── permission_ops.py # PermissionOpsMixin: has/get/require_permission wrappers
├── invite_ops.py     # InviteOpsMixin: server invite listing
├── converters.py     # Pure converter functions: _row_to_*, _server_to_dict, _dict_to_server
└── README.md         # This file
```

### How it fits together

- `protocol.py` defines `ServerProtocol` declaring all cross-mixin attributes (cache prefixes, _db, _messaging, helper methods like _is_member, _log_audit, etc.)
- `base.py` defines `ServerManager` composing all mixins: `ServerManager(ServerOpsMixin, ChannelOpsMixin, AuditOpsMixin, CacheOpsMixin, MemberOpsMixin, PermissionOpsMixin, InviteOpsMixin, BaseManager)`
- Sub-handlers (`AuditHandler`, `ChannelHandler`, `RoleHandler`, `MemberHandler`) are instantiated in `__init__` and accessed via `self.*_handler`.
- `converters.py` contains standalone conversion functions (not methods) imported where needed.

## Usage

```python
from src.core.servers.manager import ServerManager

sm = ServerManager(db, auth_module=auth, messaging_module=messaging)

# Create a server
server = sm.create_server(user_id=1, name="My Server", description="A cool place")

# Create a channel
channel = sm.create_channel(user_id=1, server_id=server.id, name="general", channel_type="text")

# Invite a user
invite = sm.create_invite(user_id=1, channel_id=channel.id, max_age=86400, max_uses=10)

# Use invite to join
member = sm.use_invite(user_id=2, code=invite.code)
```

Permission checking:

```python
if not sm.has_permission(user_id=2, server_id=server.id, permission="channels.view", channel_id=channel.id):
    raise PermissionDeniedError("Missing channels.view permission")

# Or use require_permission to raise automatically
sm.require_permission(user_id=1, server_id=server.id, permission="server.manage")
```

## Error Handling

All server operations raise domain-specific exceptions from `src.core.servers.exceptions`:

- `ServerNotFoundError` — Server ID does not exist or is deleted.
- `ServerAccessDeniedError` — User is not a member and attempts a member-only operation.
- `ChannelNotFoundError` — Channel ID not found.
- `ChannelTypeError` — Operation is invalid for the given channel type.
- `MemberNotFoundError` — User is not a member of the server.
- `InvalidServerNameError` — Server name fails length or content validation.
- `InvalidChannelNameError` — Channel name is empty, non-ASCII, or fails validation.
- `PermissionDeniedError` — User lacks the required permission, includes the permission name.
- `OwnerCannotLeaveError` — Server owner attempts to leave without transferring ownership.
- `MemberExistsError` — Adding a user who is already a member.
- `InviteNotFoundError` — Invite code does not exist.
- `InviteMaxUsesError` — Invite has reached its maximum usage count.
- `UserBannedError` — Banned user attempts to join via invite.
- `BanNotFoundError` — Unban target is not currently banned.
- `BanExistsError` — Attempting to ban an already-banned user.
- `RoleHierarchyError` — Operation violates role position hierarchy.
- `DefaultRoleError` — Attempting to delete or modify the `@everyone` default role.
- `InvalidRoleNameError` — Role name fails validation.
- `CannotModifyOwnerError` — Attempting to modify the server owner's roles.

```python
try:
    server = sm.create_server(user_id=1, name="")
except InvalidServerNameError as e:
    print(f"Server name error: {e}")

try:
    member = sm.use_invite(user_id=2, code="invalid")
except InviteNotFoundError:
    print("Invite not found")
except UserBannedError:
    print("You are banned from this server")
```

## Dependencies
- Core database cache helpers for hot-path lookups (TTL-based member, permission, channel, and owner caches).
- Server models, permissions, and exceptions from the `servers` package.
- Messaging module for channel message hooks (removing participants on leave/kick).
- Sub-handlers: `AuditHandler`, `ChannelHandler`, `RoleHandler`, `MemberHandler` for delegated operations.

## Data and Caching
- Uses in-memory caches for members, permissions, channels, and ownership.
- TTL-based cache entries (default 60s) reduce repeated DB queries.
- Cache invalidation occurs on membership and channel changes (prefix-based pattern invalidation in Redis).
- Server owner ID is cached separately with a longer TTL (3600s in Redis).
- Member existence check uses a multi-layer cache (in-memory L1 -> Redis L2 -> database).
