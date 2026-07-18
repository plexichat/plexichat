# Servers Module

Modular server management package with thin re-exports in `__init__.py`.

## Structure

```
servers/
├── __init__.py          # Thin re-exports, setup(), _get_manager(), server ops
├── channels.py          # create/get/update/delete/move channels & categories
├── roles.py             # create/get/update/delete/move roles
├── members.py           # add/get/update/remove members, kick/ban/unban, role assignment
├── invites.py           # create/get/use/delete invites
├── events.py            # scheduled events: create/get/update/delete, RSVP
├── templates.py         # templates: create/get/update/delete/apply/preview
├── onboarding.py        # welcome screen, onboarding steps & progress
├── messages.py          # send/get channel messages, audit log
├── permissions.py       # has_permission, get_permissions, require_permission, channel overrides
├── models.py            # Data models (Server, Channel, Role, Member, etc.)
├── exceptions.py        # Exception classes
├── composer.py          # ServersManager (MRO of all mixins)
├── base.py              # ServersManagerBase (holds sub-managers)
├── *_mixin.py           # Mixins for each domain
├── events.py (legacy)   # ScheduledEventManager implementation
├── templates.py (legacy) # TemplateManager implementation
├── onboarding.py (legacy) # OnboardingManager implementation
└── permissions.py (legacy) # Permission utilities
```

## Usage

```python
# Setup once in main.py
from src.core import servers
servers.setup(db, auth, messaging)

# Use anywhere via direct imports
from src.core.servers import (
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

## API Modules

| Module | Operations |
|--------|------------|
| `channels.py` | `create_channel`, `create_category`, `get_channel`, `channel_exists`, `get_channels`, `update_channel`, `delete_channel`, `move_channel` |
| `roles.py` | `create_role`, `get_role`, `get_roles`, `update_role`, `delete_role`, `move_role` |
| `members.py` | `add_member`, `get_member`, `get_members`, `get_member_user_ids`, `get_all_shared_member_ids`, `update_member`, `remove_member`, `leave_server`, `kick_member`, `ban_member`, `unban_member`, `get_bans`, `assign_role`, `remove_role`, `get_member_roles` |
| `invites.py` | `create_invite`, `get_invite`, `get_invites`, `use_invite`, `delete_invite` |
| `events.py` | `create_scheduled_event`, `get_scheduled_event`, `get_scheduled_events`, `update_scheduled_event`, `delete_scheduled_event`, `rsvp_event`, `remove_rsvp`, `get_event_rsvps`, `generate_recurring_instances` |
| `templates.py` | `create_template`, `get_template`, `get_template_by_id`, `get_user_templates`, `get_public_templates`, `preview_template`, `apply_template`, `delete_template`, `update_template` |
| `onboarding.py` | `set_welcome_screen`, `get_welcome_screen`, `delete_welcome_screen`, `create_onboarding_step`, `get_onboarding_step`, `get_onboarding_steps`, `update_onboarding_step`, `delete_onboarding_step`, `start_onboarding`, `complete_onboarding_step`, `get_onboarding_progress`, `reset_onboarding_progress` |
| `messages.py` | `send_channel_message`, `get_channel_messages`, `get_audit_log` |
| `permissions.py` | `get_channel_override`, `set_channel_override`, `delete_channel_override`, `has_permission`, `get_permissions`, `require_permission` |

## Core Server Operations (in `__init__.py`)

| Function | Description |
|----------|-------------|
| `create_server` | Create a new server |
| `get_server` | Get server by ID (with access check) |
| `get_servers` | Get all servers for a user |
| `server_exists` | Check if server exists (no permission check) |
| `update_server` | Update server settings |
| `delete_server` | Delete server (owner only) |
| `transfer_ownership` | Transfer server ownership |

## Setup

```python
from src.core.servers import setup, ServersManager

# Module-level setup (for functional API)
setup(db, auth_module, messaging_module)

# Or direct manager instantiation (for OO API)
manager = ServersManager(db, auth_module, messaging_module)
```