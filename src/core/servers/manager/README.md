# Server manager

The server manager implements the core domain logic for servers, channels, roles, members, permissions, invites, bans, and audit logging. It composes mixins for each concern and delegates specialized work to sub-handlers.

## Architecture

### Module layout

```
manager/
├── __init__.py            # Re-export: from .base import ServerManager
├── base.py                # ServerManager class (initialization and delegating methods)
├── protocol.py            # ServerProtocol: interface that declares cross-mixin attributes
├── server_ops.py          # ServerOpsMixin: server CRUD (create, get, update, delete, transfer ownership)
├── channel_ops.py         # ChannelOpsMixin: channel messaging (send/read messages, slowmode)
├── audit_ops.py           # AuditOpsMixin: audit log retrieval
├── cache_ops.py           # CacheOpsMixin: cache helpers and validation helpers
├── member_ops.py          # MemberOpsMixin: member iteration and removal
├── permission_ops.py      # PermissionOpsMixin: has/get/require_permission wrappers
├── invite_ops.py          # InviteOpsMixin: server invite listing
├── onboarding_ops.py      # OnboardingManager: onboarding steps
├── scheduled_event_ops.py # ScheduledEventManager: scheduled events
├── template_ops.py        # TemplateManager: server templates
└── converters.py          # Pure converter functions: _row_to_*, _server_to_dict, _dict_to_server
```

### How the pieces fit together

* `protocol.py` defines `ServerProtocol`, which declares the attributes that mixins share, such as cache prefixes, `_db`, `_messaging`, and helpers like `_is_member` and `_log_audit`.
* `base.py` defines `ServerManager`, which composes all mixins:

  ```python
  class ServerManager(
      ServerOpsMixin, ChannelOpsMixin, AuditOpsMixin, CacheOpsMixin,
      MemberOpsMixin, PermissionOpsMixin, InviteOpsMixin,
      OnboardingMixin, EventMixin, TemplateMixin, WelcomeMixin,
      BaseManager,
  ):
  ```

  See `base.py:77`.
* Sub-handlers (`AuditHandler`, `ChannelHandler`, `RoleHandler`, `MemberHandler` from `src/core/servers/handlers/`) are instantiated in `ServerManager.__init__` and accessed as `self.audit_handler`, `self.channel_handler`, and so on. See `base.py:177`.
* `converters.py` provides standalone conversion functions that are not methods, such as `_row_to_channel` and `_row_to_audit_entry`.

## Setup

```python
from src.core.servers.manager import ServerManager

sm = ServerManager(db, auth_module=auth, messaging_module=messaging)

# Optional modules wired in base.py:146
sm = ServerManager(
    db,
    auth_module=auth,
    messaging_module=messaging,
    notifications_module=notifications,
    events_module=events,
)
```

Configuration is loaded from the global `servers` key in `config.yaml`. Defaults are defined in `base.py:187`:

| Key | Default | Description |
|---|---|---|
| `max_servers_per_user` | `100` | Maximum servers a user can own. |
| `max_channels_per_server` | `500` | Maximum channels per server. |
| `max_roles_per_server` | `250` | Maximum roles per server. |
| `max_members_per_server` | `250000` | Maximum members per server. |
| `server_name_min_length` | `2` | Minimum server name length. |
| `server_name_max_length` | `100` | Maximum server name length. |
| `channel_name_min_length` | `1` | Minimum channel name length. |
| `channel_name_max_length` | `100` | Maximum channel name length. |
| `role_name_min_length` | `1` | Minimum role name length. |
| `role_name_max_length` | `100` | Maximum role name length. |
| `invite_code_length` | `8` | Length of generated invite codes. |

Encryption of descriptions and thread names is controlled by `encryption.encrypt_descriptions` and `encryption.encrypt_thread_names`. See `base.py:161`.

## Usage

### Create a server and channel

```python
from src.core.servers.manager import ServerManager

sm = ServerManager(db, auth_module=auth, messaging_module=messaging)

server = sm.create_server(user_id=1, name="My Server", description="A cool place")

channel = sm.create_channel(
    user_id=1,
    server_id=server.id,
    name="general",
    channel_type="text",
)
```

### Work with invites

```python
invite = sm.create_invite(
    user_id=1, channel_id=channel.id, max_age=86400, max_uses=10
)

member = sm.use_invite(user_id=2, code=invite.code)
```

### Check permissions

Use `has_permission` when you need a boolean, or `require_permission` when you want the call to raise on failure.

```python
if not sm.has_permission(
    user_id=2,
    server_id=server.id,
    permission="channels.view",
    channel_id=channel.id,
):
    raise PermissionDeniedError("Missing channels.view permission")

# Raise PermissionDeniedError automatically if the check fails
sm.require_permission(user_id=1, server_id=server.id, permission="server.manage")
```

### Channel-specific operations

```python
# Retrieve a channel (returns None if not a member or missing permission)
channel = sm.get_channel(channel_id, user_id)

# Membership-agnostic existence check (distinguishes 404 from 403)
if not sm.channel_exists(channel_id):
    raise ChannelNotFoundError("Channel does not exist")

# Map between channels and conversations without membership filtering
conversation_id = sm.get_channel_conversation_id(channel_id)
info = sm.get_channel_by_conversation(conversation_id)  # {"id": ..., "server_id": ...}
```

See `base.py:260` and `base.py:305` for details.

## Error handling

All operations raise domain-specific exceptions from `src.core.servers.exceptions`. Catch the specific exception your caller can handle.

| Exception | When it is raised |
|---|---|
| `ServerNotFoundError` | Server ID does not exist or is deleted. |
| `ServerAccessDeniedError` | User is not a member and attempts a member-only operation. |
| `ChannelNotFoundError` | Channel ID not found. |
| `ChannelTypeError` | Operation is invalid for the given channel type. |
| `CategoryNotFoundError` | Category ID not found. |
| `MemberNotFoundError` | User is not a member of the server. |
| `MemberExistsError` | Adding a user who is already a member. |
| `InvalidServerNameError` | Server name fails length or content validation. |
| `InvalidChannelNameError` | Channel name is empty, non-ASCII, or fails validation. |
| `InvalidChannelSettingError` | Channel setting (such as `slowmode_seconds`) is out of range. |
| `PermissionDeniedError` | User lacks the required permission; includes the permission name. |
| `OwnerCannotLeaveError` | Server owner attempts to leave without transferring ownership. |
| `CannotModifyOwnerError` | Attempting to modify the server owner's roles. |
| `InviteNotFoundError` | Invite code does not exist. |
| `InviteExpiredError` | Invite has expired. |
| `InviteMaxUsesError` | Invite has reached its maximum usage count. |
| `UserBannedError` | Banned user attempts to join through an invite. |
| `BanNotFoundError` | Unban target is not currently banned. |
| `BanExistsError` | Attempting to ban an already-banned user. |
| `RoleNotFoundError` | Role ID not found. |
| `RoleHierarchyError` | Operation violates role position hierarchy. |
| `DefaultRoleError` | Attempting to delete or modify the `@everyone` default role. |
| `InvalidRoleNameError` | Role name fails validation. |
| `InvalidPermissionKeyError` | Permission key is not in the known vocabulary. |

Example:

```python
from src.core.servers.exceptions import InvalidServerNameError, InviteNotFoundError, UserBannedError

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

`ServerManager` also re-exports these exceptions as class attributes for convenience (for example, `ServerManager.ServerNotFoundError`). See `base.py:100`.

## Caching

The manager uses multi-layer caching to reduce database queries:

* **In-memory L1 cache** — Per-process cache for members, permissions, channels, and ownership. TTL is `60` seconds by default. See `base.py:169` and `src/core/servers/manager/cache_ops.py`.
* **Valkey/Redis L2 cache** — Shared cache for permission snapshots (`perms:{user_id}:{server_id}:{channel_id}`), channel lists, and server ownership. Used when `valkey_available()` returns true.
* **Invalidation** — Prefix-based invalidation on membership and channel changes (for example, `invalidate_pattern("server_channels:*")` and `invalidate_pattern("perms:*")`). Role updates invalidate the entire server's permission snapshots so revocations take effect immediately.

Member existence checks use the full L1 → L2 → database path. Server owner ID is cached separately with a longer effective TTL.

## Dependencies

* Database handle (`db`) with `fetch_one`, `fetch_all`, `execute`, and cache helpers.
* Server models, permissions, and exceptions from the `src.core.servers` package.
* Messaging module for channel message hooks, such as removing participants on leave or kick.
* Optional notifications and events modules for scheduled events.
* Sub-handlers: `AuditHandler`, `ChannelHandler`, `RoleHandler`, `MemberHandler`.

## Related code

* `src/core/servers/handlers/` — Specialized handlers that the manager delegates to.
* `src/core/servers/manager/converters.py` — Pure row-to-model converters.
* `src/core/servers/models.py` — Domain models (`Server`, `Channel`, `Role`, `Member`, `Invite`, `Ban`).
* `src/core/servers/permission_utils.py` — Permission calculation logic.
