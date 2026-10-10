# Servers module

Use the servers module to manage Plexichat servers, channels, roles, members, invites, and related resources. The package exposes a thin functional API in `__init__.py` and a class-based API through `ServersManager`.

## Architecture

`ServersManager` at `plexichat/src/core/servers/composer.py:19` is a mixin composition that inherits from `ServersManagerBase` at `plexichat/src/core/servers/base.py:12`. The base class holds the four underlying managers and shared configuration:

*   `ServerManager` at `plexichat/src/core/servers/manager/base.py` — core server, channel, role, and member operations.
*   `ScheduledEventManager` at `plexichat/src/core/servers/manager/scheduled_event_ops.py` — scheduled event operations.
*   `TemplateManager` at `plexichat/src/core/servers/manager/template_ops.py` — template operations.
*   `OnboardingManager` at `plexichat/src/core/servers/manager/onboarding_ops.py` — onboarding and welcome screen operations.

Domain mixins contribute one responsibility each:

| Mixin file | Domain |
| --- | --- |
| `server_mixin.py` | `ServerMixin` — create, get, update, delete, transfer ownership |
| `channel_mixin.py` | `ChannelMixin` — channel and category CRUD plus move |
| `role_mixin.py` | `RoleMixin` — role CRUD plus move and hierarchy checks |
| `member_mixin.py` | `MemberMixin` — add, get, update, remove, kick, ban, unban |
| `role_assignment_mixin.py` | `RoleAssignmentMixin` — assign, remove, and list member roles |
| `permission_mixin.py` | `PermissionMixin` — permission checks and channel overrides |
| `invite_mixin.py` | `InviteMixin` — invite lifecycle |
| `messaging_mixin.py` | `MessagingMixin` — channel messaging |
| `audit_mixin.py` | `AuditMixin` — audit log |
| `event_mixin.py` | `EventMixin` — scheduled events and RSVPs |
| `template_mixin.py` | `TemplateMixin` — templates |
| `welcome_mixin.py` | `WelcomeMixin` — welcome screens |
| `onboarding_mixin.py` | `OnboardingMixin` — onboarding steps and progress |

## Before you begin

Initialize the module once during application startup:

```python
from plexichat.src.core.servers import setup

setup(db, auth_module, messaging_module, notifications_module, events_module)
```

*   `db` is required. The remaining modules are optional and enable permission checks, messaging, and notifications.
*   If you call any functional API before `setup()` completes, `_get_manager()` at `plexichat/src/core/servers/__init__.py:413` raises `RuntimeError`.

To use the class-based API directly, instantiate the manager:

```python
from plexichat.src.core.servers.composer import ServersManager

manager = ServersManager(db, auth_module, messaging_module)
server = manager.create_server(owner_id=1, name="My Server")
```

## Use the functional API

Import functions from `plexichat.src.core.servers` and call them from any module after `setup()`:

```python
from plexichat.src.core.servers import (
    create_server,
    create_channel,
    add_member,
    create_invite,
    has_permission,
)

server = create_server(owner_id=1, name="My Server")
channel = create_channel(user_id=1, server_id=server.id, name="general")
add_member(server_id=server.id, user_id=2)
invite = create_invite(user_id=1, channel_id=channel.id)
```

## API reference

### Core server operations

Defined in `plexichat/src/core/servers/__init__.py:423`:

| Function | Description |
| --- | --- |
| `create_server(owner_id, name, ...)` | Create a new server. Returns `Server`. |
| `get_server(server_id, user_id)` | Get a server by ID if the user has access. Returns `Optional[Server]`. |
| `get_servers(user_id, limit=100)` | List all servers that the user is a member of. |
| `server_exists(server_id)` | Check whether a server exists without a permission check. |
| `update_server(user_id, server_id, ...)` | Update server settings such as name, description, or verification level. |
| `delete_server(user_id, server_id)` | Delete a server. Owner only. |
| `transfer_ownership(user_id, server_id, new_owner_id)` | Transfer ownership to another member. |

### Domain modules

Re-exported from submodules through `plexichat/src/core/servers/__init__.py:85`:

| Module | Source file | Operations |
| --- | --- | --- |
| Channels | `channels.py` | `create_channel`, `create_category`, `get_channel`, `channel_exists`, `get_channel_conversation_id`, `get_channel_by_conversation`, `get_channels`, `update_channel`, `delete_channel`, `move_channel` |
| Roles | `roles.py` | `create_role`, `get_role`, `get_roles`, `update_role`, `delete_role`, `move_role` |
| Members | `members.py` | `add_member`, `get_member`, `get_members`, `get_member_user_ids`, `get_all_shared_member_ids`, `update_member`, `remove_member`, `leave_server`, `kick_member`, `ban_member`, `unban_member`, `get_bans`, `assign_role`, `remove_role`, `get_member_roles` |
| Invites | `invites.py` | `create_invite`, `get_invite`, `get_invites`, `use_invite`, `delete_invite` |
| Scheduled events | `events.py` | `create_scheduled_event`, `get_scheduled_event`, `get_scheduled_events`, `update_scheduled_event`, `delete_scheduled_event`, `rsvp_event`, `remove_rsvp`, `get_event_rsvps`, `generate_recurring_instances` |
| Templates | `templates.py` | `create_template`, `get_template`, `get_template_by_id`, `get_user_templates`, `get_public_templates`, `preview_template`, `apply_template`, `delete_template`, `update_template` |
| Onboarding | `onboarding.py` | `set_welcome_screen`, `get_welcome_screen`, `delete_welcome_screen`, `create_onboarding_step`, `get_onboarding_step`, `get_onboarding_steps`, `update_onboarding_step`, `delete_onboarding_step`, `start_onboarding`, `complete_onboarding_step`, `get_onboarding_progress`, `reset_onboarding_progress` |
| Messages | `messages.py` | `send_channel_message`, `get_channel_messages`, `get_audit_log` |
| Permissions | `permissions.py` | `get_channel_override`, `set_channel_override`, `delete_channel_override`, `has_permission`, `get_permissions`, `require_permission` |

## Models and exceptions

Import data models from `plexichat/src/core/servers/models.py` and exceptions from `plexichat/src/core/servers/exceptions.py`:

```python
from plexichat.src.core.servers.models import Server, Channel, Role, Member
from plexichat.src.core.servers.exceptions import ServerNotFoundError, PermissionDeniedError
```

See `plexichat/src/core/servers/models.py` for the full list, including `Server`, `Channel`, `ChannelCategory`, `Role`, `Member`, `ChannelOverride`, `Invite`, `Ban`, `AuditLogEntry`, `ScheduledEvent`, `ServerTemplate`, `WelcomeScreen`, and `OnboardingProgress`.

## Configuration

`ServersManagerBase._load_config()` at `plexichat/src/core/servers/base.py:77` merges defaults with `utils.config.get("servers", {})`:

| Key | Default | Description |
| --- | --- | --- |
| `max_servers_per_user` | `100` | Maximum servers per user. |
| `max_channels_per_server` | `500` | Maximum channels per server. |
| `max_roles_per_server` | `250` | Maximum roles per server. |
| `max_members_per_server` | `250000` | Maximum members per server. |
| `server_name_min_length` | `2` | Minimum server name length. |
| `server_name_max_length` | `100` | Maximum server name length. |

On startup, `setup()` at `plexichat/src/core/servers/__init__.py:396` also calls `backfill_default_role_permissions()` at `plexichat/src/core/servers/__init__.py:342`. This function merges any new keys from `DEFAULT_EVERYONE_PERMISSIONS` into existing `@everyone` roles without overwriting explicit `False` values.

## Related documentation

*   `plexichat/src/core/servers/composer.py:19` — full mixin inheritance list.
*   `plexichat/src/core/servers/base.py:12` — sub-manager initialization and audit helpers.
*   `plexichat/src/core/servers/models.py` — data models and permission constants.
*   `plexichat/src/core/servers/exceptions.py` — exception hierarchy for server operations.
