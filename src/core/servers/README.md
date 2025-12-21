# Servers Module

Server-specific permissions system for PlexiChat API supporting community servers with channels, roles, and permission overrides.

## Features

- Server (guild) creation and management
- Text and voice channels with categories
- Hierarchical role system with position-based authority
- Channel-specific permission overrides
- Member management with kicks and bans
- Invite system with expiration and usage limits
- Comprehensive audit logging
- Integration with messaging module for channel messages
- Scheduled events with RSVP tracking and recurring support
- Server templates for cloning server structure
- Welcome screens for new members
- Onboarding flows with customizable steps

## Installation

Requires the following packages (already installed for auth/messaging modules):

```bash
pip install argon2-cffi cryptography PyYAML
```

## Setup

```python
from src.core.database import Database
from src.core import auth
from src.core import messaging
from src.core import servers

# Initialize database
db = Database()
db.connect()

# Initialize auth first
auth.setup(db)

# Initialize messaging
messaging.setup(db, auth)

# Initialize servers (with optional notifications and events modules)
servers.setup(db, auth, messaging, notifications_module=None, events_module=None)
```

## Usage

### Server Management

```python
from src.core import servers

# Create a server
server = servers.create_server(
    owner_id=user_id,
    name="My Server",
    description="A cool community server"
)

# Get server info
server = servers.get_server(server_id, user_id)

# Update server
server = servers.update_server(
    user_id=owner_id,
    server_id=server.id,
    name="New Name",
    description="Updated description"
)

# Get all servers user is in
my_servers = servers.get_servers(user_id)

# Transfer ownership
servers.transfer_ownership(owner_id, server_id, new_owner_id)

# Delete server (owner only)
servers.delete_server(owner_id, server_id)
```

### Channels

```python
# Create a text channel
channel = servers.create_channel(
    user_id=owner_id,
    server_id=server.id,
    name="general",
    channel_type=servers.ChannelType.TEXT,
    topic="General discussion"
)

# Create a category
category = servers.create_category(
    user_id=owner_id,
    server_id=server.id,
    name="Text Channels"
)

# Create channel in category
channel = servers.create_channel(
    user_id=owner_id,
    server_id=server.id,
    name="announcements",
    channel_type=servers.ChannelType.TEXT,
    category_id=category.id,
    nsfw=False,
    slowmode_seconds=0
)

# Get channels
channels = servers.get_channels(user_id, server_id)

# Update channel
channel = servers.update_channel(
    user_id=owner_id,
    channel_id=channel.id,
    topic="Important announcements only"
)

# Delete channel
servers.delete_channel(owner_id, channel_id)
```

### Roles

```python
# Create a role
role = servers.create_role(
    user_id=owner_id,
    server_id=server.id,
    name="Moderator",
    permissions={
        "messages.manage": True,
        "members.kick": True,
        "members.ban": True
    },
    color="#FF0000",
    hoist=True,  # Show separately in member list
    mentionable=True
)

# Get roles (ordered by position, highest first)
roles = servers.get_roles(user_id, server_id)

# Update role
role = servers.update_role(
    user_id=owner_id,
    role_id=role.id,
    permissions={"administrator": True}
)

# Move role in hierarchy
role = servers.move_role(owner_id, role_id, position=5)

# Delete role
servers.delete_role(owner_id, role_id)
```

### Members

```python
# Add member (usually via invite)
member = servers.add_member(server_id, user_id)

# Get member
member = servers.get_member(server_id, user_id)

# Get all members
members = servers.get_members(user_id, server_id, limit=100)

# Update member nickname
member = servers.update_member(
    user_id=mod_id,
    server_id=server_id,
    member_user_id=target_id,
    nickname="Cool Nickname"
)

# Leave server
servers.remove_member(user_id, server_id)

# Kick member
servers.kick_member(mod_id, server_id, target_id, reason="Rule violation")

# Ban member
ban = servers.ban_member(
    user_id=mod_id,
    server_id=server_id,
    member_user_id=target_id,
    reason="Repeated violations",
    delete_message_days=7
)

# Unban
servers.unban_member(mod_id, server_id, banned_user_id)

# Get bans
bans = servers.get_bans(mod_id, server_id)
```

### Role Assignment

```python
# Assign role to member
servers.assign_role(mod_id, server_id, member_user_id, role_id)

# Remove role from member
servers.remove_role(mod_id, server_id, member_user_id, role_id)

# Get member's roles
roles = servers.get_member_roles(server_id, member_user_id)
```

### Permissions

```python
# Check permission
if servers.has_permission(user_id, server_id, "messages.send"):
    # User can send messages
    pass

# Check channel-specific permission
if servers.has_permission(user_id, server_id, "messages.send", channel_id):
    # User can send in this specific channel
    pass

# Require permission (raises PermissionDeniedError if missing)
servers.require_permission(user_id, server_id, "members.kick")

# Get all permissions
perms = servers.get_permissions(user_id, server_id, channel_id)
```

### Channel Permission Overrides

```python
# Set role override for a channel
override = servers.set_channel_override(
    user_id=owner_id,
    channel_id=channel_id,
    target_type="role",
    target_id=role_id,
    allow={"messages.send": True},
    deny={"messages.mention_everyone": True}
)

# Set member override
override = servers.set_channel_override(
    user_id=owner_id,
    channel_id=channel_id,
    target_type="member",
    target_id=member_user_id,
    deny={"messages.send": True}  # Mute this user in this channel
)

# Delete override
servers.delete_channel_override(owner_id, channel_id, "role", role_id)
```

### Invites

```python
# Create invite
invite = servers.create_invite(
    user_id=user_id,
    channel_id=channel_id,
    max_age=86400,  # 24 hours (0 = never expires)
    max_uses=10,    # 0 = unlimited
    temporary=False
)
print(f"Invite code: {invite.code}")

# Get invite info
invite = servers.get_invite(code)

# Use invite to join
member = servers.use_invite(user_id, code)

# Get all server invites
invites = servers.get_invites(mod_id, server_id)

# Delete invite
servers.delete_invite(mod_id, code)
```

### Channel Messaging

```python
# Send message to channel
msg = servers.send_channel_message(
    user_id=user_id,
    channel_id=channel_id,
    content="Hello everyone!"
)

# Get channel messages
messages = servers.get_channel_messages(
    user_id=user_id,
    channel_id=channel_id,
    limit=50
)
```

### Audit Log

```python
# Get audit log
entries = servers.get_audit_log(
    user_id=mod_id,
    server_id=server_id,
    limit=50
)

# Filter by action type
kicks = servers.get_audit_log(
    user_id=mod_id,
    server_id=server_id,
    action_type=servers.AuditLogAction.MEMBER_KICK
)
```

### Scheduled Events

```python
import time

# Create a voice channel event
start_time = int(time.time() * 1000) + 3600000  # 1 hour from now
event = servers.create_scheduled_event(
    user_id=owner_id,
    server_id=server.id,
    name="Game Night",
    start_time=start_time,
    event_type=servers.ScheduledEventType.VOICE,
    description="Weekly game night",
    channel_id=voice_channel.id,
    end_time=start_time + 7200000,  # 2 hours duration
)

# Create an external location event
event = servers.create_scheduled_event(
    user_id=owner_id,
    server_id=server.id,
    name="Community Meetup",
    start_time=start_time,
    event_type=servers.ScheduledEventType.EXTERNAL,
    location="Central Park, NYC",
)

# Create a recurring event with RRULE
event = servers.create_scheduled_event(
    user_id=owner_id,
    server_id=server.id,
    name="Weekly Standup",
    start_time=start_time,
    event_type=servers.ScheduledEventType.EXTERNAL,
    location="Virtual",
    rrule="FREQ=WEEKLY;BYDAY=MO,WE,FR",
)

# Get scheduled events
events = servers.get_scheduled_events(user_id, server_id)

# Get events by status
active_events = servers.get_scheduled_events(
    user_id, server_id, status=servers.ScheduledEventStatus.ACTIVE
)

# Update event
event = servers.update_scheduled_event(
    user_id=owner_id,
    event_id=event.id,
    name="Updated Event Name",
    status=servers.ScheduledEventStatus.CANCELLED,
)

# Delete event
servers.delete_scheduled_event(owner_id, event.id)

# Generate recurring event instances
instances = servers.generate_recurring_instances(event.id, owner_id, count=10)
```

### Event RSVP

```python
# RSVP to an event
rsvp = servers.rsvp_event(user_id, event.id, servers.RSVPStatus.GOING)
rsvp = servers.rsvp_event(user_id, event.id, servers.RSVPStatus.INTERESTED)

# Remove RSVP
servers.remove_rsvp(user_id, event.id)

# Get RSVPs for an event
rsvps = servers.get_event_rsvps(user_id, event.id)

# Filter by status
going = servers.get_event_rsvps(user_id, event.id, status=servers.RSVPStatus.GOING)

# Event counts are updated automatically
event = servers.get_scheduled_event(event.id, user_id)
print(f"Interested: {event.interested_count}, Going: {event.going_count}")
```

### Server Templates

```python
# Create a template from an existing server
template = servers.create_template(
    user_id=owner_id,
    server_id=server.id,
    name="Gaming Community Template",
    description="Perfect for gaming communities",
)
print(f"Template code: {template.code}")

# Get template by code
template = servers.get_template(code)

# Preview template without applying
preview = servers.preview_template(code)
print(f"Channels: {len(preview.channels)}")
print(f"Roles: {len(preview.roles)}")

# Apply template to create a new server
new_server = servers.apply_template(
    user_id=user_id,
    code=template.code,
    server_name="My New Server",
    server_description="Created from template",
)

# Get user's templates
templates = servers.get_user_templates(user_id)

# Get public templates
public = servers.get_public_templates()

# Update template
template = servers.update_template(
    user_id=owner_id,
    code=template.code,
    is_public=True,
)

# Delete template
servers.delete_template(owner_id, template.code)
```

### Welcome Screen

```python
# Set welcome screen for a server
welcome = servers.set_welcome_screen(
    user_id=owner_id,
    server_id=server.id,
    description="Welcome to our community!",
    welcome_channels=[
        {"channel_id": general.id, "description": "Start chatting here"},
        {"channel_id": announcements.id, "description": "Important updates"},
    ],
    enabled=True,
)

# Get welcome screen
welcome = servers.get_welcome_screen(server.id, user_id)

# Disable welcome screen
welcome = servers.set_welcome_screen(
    user_id=owner_id,
    server_id=server.id,
    enabled=False,
)

# Delete welcome screen
servers.delete_welcome_screen(owner_id, server.id)
```

### Onboarding

```python
# Create onboarding steps
rules_step = servers.create_onboarding_step(
    user_id=owner_id,
    server_id=server.id,
    step_type=servers.OnboardingStepType.READ_RULES,
    title="Read Our Rules",
    description="Please read and accept our community rules",
    required=True,
)

roles_step = servers.create_onboarding_step(
    user_id=owner_id,
    server_id=server.id,
    step_type=servers.OnboardingStepType.SELECT_ROLES,
    title="Choose Your Interests",
    options={"role_ids": [gamer_role.id, artist_role.id]},
)

channel_step = servers.create_onboarding_step(
    user_id=owner_id,
    server_id=server.id,
    step_type=servers.OnboardingStepType.VISIT_CHANNEL,
    title="Check Out Introductions",
    options={"channel_id": intro_channel.id},
)

# Get onboarding steps
steps = servers.get_onboarding_steps(user_id, server.id)

# Update step
step = servers.update_onboarding_step(
    user_id=owner_id,
    step_id=step.id,
    required=True,
    position=0,
)

# Delete step
servers.delete_onboarding_step(owner_id, step.id)

# Start onboarding for a user
progress = servers.start_onboarding(user_id, server.id)

# Complete a step
progress = servers.complete_onboarding_step(
    user_id=user_id,
    server_id=server.id,
    step_id=rules_step.id,
)

# Complete select roles step with response
progress = servers.complete_onboarding_step(
    user_id=user_id,
    server_id=server.id,
    step_id=roles_step.id,
    response={"selected_roles": [gamer_role.id]},
)

# Get onboarding progress
progress = servers.get_onboarding_progress(user_id, server.id)
print(f"Completed: {progress.completed}")
print(f"Steps done: {progress.completed_steps}")

# Reset progress
servers.reset_onboarding_progress(user_id, server.id)
```

## Configuration

All settings are in `config/config.yaml` under `servers`:

```yaml
servers:
  max_servers_per_user: 100
  max_channels_per_server: 500
  max_roles_per_server: 250
  max_members_per_server: 250000
  server_name_min_length: 2
  server_name_max_length: 100
  channel_name_min_length: 1
  channel_name_max_length: 100
  role_name_min_length: 1
  role_name_max_length: 100
  invite_code_length: 8

  events:
    max_events_per_server: 100
    max_event_duration_hours: 168
    reminder_minutes: [60, 15]
    max_recurring_instances: 50

  templates:
    max_templates_per_user: 25
    template_code_length: 8
    max_channels_in_template: 100
    max_roles_in_template: 50

  onboarding:
    max_onboarding_steps: 10
    max_welcome_channels: 5
    max_step_options: 25
```

## Permission System

### Permission Calculation

1. Base permissions from roles (union of all role permissions)
2. Administrator permission bypasses all checks
3. Server owner has all permissions
4. Channel overrides modify base permissions:
   - Role overrides applied first (deny, then allow)
   - Member override applied last (deny, then allow)

### Available Permissions

| Category | Permission | Description |
|----------|------------|-------------|
| server | server.manage | Manage server settings |
| server | server.view_audit_log | View audit log |
| channels | channels.manage | Create, edit, delete channels |
| channels | channels.view | View channels |
| roles | roles.manage | Create, edit, delete roles |
| members | members.kick | Kick members |
| members | members.ban | Ban members |
| members | members.manage_nicknames | Change other members' nicknames |
| members | members.manage_roles | Assign and remove roles |
| invites | invites.create | Create invites |
| invites | invites.manage | Manage and delete invites |
| messages | messages.send | Send messages |
| messages | messages.read | Read messages |
| messages | messages.manage | Delete other members' messages |
| messages | messages.embed_links | Embed links |
| messages | messages.attach_files | Attach files |
| messages | messages.mention_everyone | Use @everyone and @here |
| messages | messages.add_reactions | Add reactions |
| voice | voice.connect | Connect to voice channels |
| voice | voice.speak | Speak in voice channels |
| voice | voice.mute_members | Mute other members |
| voice | voice.deafen_members | Deafen other members |
| voice | voice.move_members | Move members between channels |
| events | events.manage | Create, edit, delete scheduled events |
| events | events.view | View scheduled events |
| templates | templates.manage | Create and manage server templates |
| onboarding | onboarding.manage | Manage welcome screen and onboarding |
| admin | administrator | Full administrator access |

### Role Hierarchy

- Roles have positions (higher = more authority)
- Users can only manage roles below their highest role
- Users can only kick/ban members with lower role positions
- Server owner bypasses all hierarchy checks

## Error Handling

All server errors inherit from `ServerError`:

```python
from src.core.servers import (
    ServerError,
    ServerNotFoundError,
    ServerAccessDeniedError,
    ChannelNotFoundError,
    RoleNotFoundError,
    RoleHierarchyError,
    MemberNotFoundError,
    MemberExistsError,
    InviteExpiredError,
    UserBannedError,
    PermissionDeniedError,
)

try:
    servers.kick_member(user_id, server_id, target_id)
except RoleHierarchyError:
    print("Cannot kick member with equal or higher role")
except PermissionDeniedError as e:
    print(f"Missing permission: {e.permission}")
except CannotModifyOwnerError:
    print("Cannot kick the server owner")
```

## Database Schema

Tables (prefixed with `srv_`):
- `srv_servers` - Server metadata
- `srv_channels` - Channels within servers
- `srv_categories` - Channel categories
- `srv_roles` - Server roles
- `srv_members` - Server membership
- `srv_member_roles` - Many-to-many member-role assignments
- `srv_channel_overrides` - Permission overrides per channel
- `srv_invites` - Server invites
- `srv_bans` - Server bans
- `srv_audit_log` - Audit log entries
- `srv_scheduled_events` - Scheduled server events
- `srv_event_rsvps` - Event RSVP tracking
- `srv_templates` - Server templates
- `srv_template_data` - Template structure snapshots
- `srv_welcome_screens` - Welcome screen configuration
- `srv_onboarding_steps` - Onboarding step definitions
- `srv_onboarding_progress` - User onboarding progress

## Testing

```bash
pytest src/tests/servers/ -v
```
