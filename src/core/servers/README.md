# Servers Module

Mixin-based server management with unified `ServersManager` interface.

## Architecture

```
servers/
├── __init__.py          # Public exports (re-exports from composer)
├── base.py               # ServersManagerBase (holds 4 manager instances)
├── composer.py           # ServersManager (MRO of all mixins)
├── server_mixin.py       # Server CRUD operations
├── channel_mixin.py      # Channel operations
├── role_mixin.py         # Role operations
├── member_mixin.py       # Member operations
├── role_assignment_mixin.py  # Role assignment operations
├── permission_mixin.py   # Permission operations
├── invite_mixin.py       # Invite operations
├── messaging_mixin.py    # Channel messaging operations
├── audit_mixin.py       # Audit log operations
├── event_mixin.py       # Scheduled event operations
├── template_mixin.py    # Template operations
├── welcome_mixin.py     # Welcome screen operations
├── onboarding_mixin.py  # Onboarding operations
├── models.py            # Data models
├── exceptions.py        # Exception classes
├── permissions.py       # Permission checking utilities
├── events.py            # ScheduledEventManager (standalone)
├── templates.py         # TemplateManager (standalone)
└── onboarding.py       # OnboardingManager (standalone)
```

## Design

The module uses a mixin-based architecture where `ServersManager` combines functionality from four underlying managers:

- **ServerManager** (`manager/base.py`): Core server, channel, role, member operations via mixins
- **ScheduledEventManager** (`events.py`): Scheduled event operations with RSVP
- **TemplateManager** (`templates.py`): Server template operations
- **OnboardingManager** (`onboarding.py`): Welcome screen and onboarding flow operations

## Usage

```python
from src.core.servers import ServersManager

# Create manager instance
servers = ServersManager(db, auth_module, messaging_module)

# Server operations
server = servers.create_server(owner_id=1, name="My Server")
servers.update_server(user_id=1, server_id=server.id, name="New Name")

# Channel operations
channel = servers.create_channel(user_id=1, server_id=server.id, name="general")

# Member operations
member = servers.add_member(server_id=server.id, user_id=2)

# Event operations
event = servers.create_scheduled_event(user_id=1, server_id=server.id, name="Meeting", start_time=1234567890)
```

## Mixins

Each mixin provides methods for a specific domain, delegating to the appropriate underlying manager:

| Mixin | Underlying Manager | Domain |
|-------|-------------------|--------|
| ServerMixin | ServerManager | Server CRUD |
| ChannelMixin | ServerManager | Channel operations |
| RoleMixin | ServerManager | Role operations |
| MemberMixin | ServerManager | Member operations |
| RoleAssignmentMixin | ServerManager | Role assignment |
| PermissionMixin | ServerManager | Permissions |
| InviteMixin | ServerManager | Invites |
| MessagingMixin | ServerManager | Messaging |
| AuditMixin | ServerManager | Audit log |
| EventMixin | ScheduledEventManager | Scheduled events |
| TemplateMixin | TemplateManager | Templates |
| WelcomeMixin | OnboardingManager | Welcome screens |
| OnboardingMixin | OnboardingManager | Onboarding flows |

## MRO

`ServersManager` uses C3 linearization for method resolution:

```
ServersManager
├── ServerMixin
├── ChannelMixin
├── RoleMixin
├── MemberMixin
├── RoleAssignmentMixin
├── PermissionMixin
├── InviteMixin
├── MessagingMixin
├── AuditMixin
├── EventMixin
├── TemplateMixin
├── WelcomeMixin
├── OnboardingMixin
└── ServersManagerBase
    └── BaseManager
```

## Backward Compatibility

The existing `manager/` subpackage remains available for direct usage:

```python
from src.core.servers.manager.base import ServerManager

manager = ServerManager(db, auth_module, messaging_module)
```

The `ServersManager` class provides a unified interface that combines all four managers.