# Permissions

Plexichat uses string-based capabilities such as `messages.send` or `admin.system`. Permissions are stored as a JSON dictionary of `permission: boolean` and checked with `has_permission()` in `src/core/auth/permissions.py`.

## How permission checks work

- `has_permission()` returns `true` if the exact permission is granted.
- `*` grants all permissions.
- `messages.*` grants all permissions that start with `messages.`.
- For `messages.send`, the check looks for `*`, `messages.send`, or `messages.*`.
- If `user_permissions` is `null` or empty, the user has no permissions.

Use `validate_permissions()` to check for unknown permissions and bot restrictions.

## Runtime summary

- Permission categories: 11
- Total permissions: 49

## Categories

Permissions are grouped by the prefix before the dot.

- **account** — profile and account management
- **admin** — system administration
- **bots** — bot account management
- **channels** — channel management within servers
- **conversations** — direct messages and group conversations
- **messages** — message actions
- **moderation** — moderation actions
- **roles** — role management within servers
- **servers** — community server management
- **video** — video calls
- **voice** — voice channels

## Permission catalog

### account

- `account.edit_profile` — Edit own profile
- `account.delete` — Delete own account (bot-restricted)
- `account.view_others` — View other user profiles

### admin

- `admin.users` — Manage all users (bot-restricted)
- `admin.servers` — Manage all servers (bot-restricted)
- `admin.system` — System administration (bot-restricted)

### bots

- `bots.create` — Create bot accounts (bot-restricted)
- `bots.manage` — Manage own bots (bot-restricted)

### channels

- `channels.create` — Create channels in servers
- `channels.manage` — Manage channel settings
- `channels.delete` — Delete channels

### conversations

- `conversations.create` — Create new conversations
- `conversations.join` — Join conversations
- `conversations.leave` — Leave conversations
- `conversations.invite` — Invite others to conversations
- `conversations.kick` — Remove others from conversations
- `conversations.manage` — Manage conversation settings
- `conversations.delete` — Delete conversations

### messages

- `messages.send` — Send messages in conversations
- `messages.read` — Read messages in conversations
- `messages.edit` — Edit own messages
- `messages.delete` — Delete own messages
- `messages.delete_others` — Delete others' messages (moderator)
- `messages.pin` — Pin messages
- `messages.react` — Add reactions to messages

### moderation

- `moderation.ban` — Ban users from servers
- `moderation.kick` — Kick users from servers
- `moderation.mute` — Mute users in servers
- `moderation.warn` — Warn users
- `moderation.view_audit` — View moderation audit log

### roles

- `roles.create` — Create roles in servers
- `roles.manage` — Manage role permissions
- `roles.assign` — Assign roles to members
- `roles.delete` — Delete roles

### servers

- `servers.create` — Create new servers
- `servers.join` — Join servers
- `servers.leave` — Leave servers
- `servers.manage` — Manage server settings
- `servers.delete` — Delete servers
- `servers.invite` — Create server invites

### video

- `video.join` — Join video calls
- `video.stream` — Stream video
- `video.initiate` — Start video calls

### voice

- `voice.join` — Join voice channels
- `voice.speak` — Speak in voice channels
- `voice.mute_others` — Mute other users
- `voice.deafen_others` — Deafen other users
- `voice.move_others` — Move users between channels
- `voice.initiate` — Start voice calls

## Default grants

### User accounts

New user accounts receive these permissions by default (`DEFAULT_USER_PERMISSIONS` in `src/core/auth/permissions.py`):

`messages.send`, `messages.read`, `messages.edit`, `messages.delete`, `messages.react`, `conversations.create`, `conversations.join`, `conversations.leave`, `conversations.invite`, `servers.create`, `servers.join`, `servers.leave`, `voice.join`, `voice.speak`, `voice.initiate`, `video.join`, `video.stream`, `video.initiate`, `account.edit_profile`, `account.delete`, `account.view_others`, `bots.create`, `bots.manage`

### Bot accounts

Bot accounts receive a smaller set (`DEFAULT_BOT_PERMISSIONS`):

`messages.send`, `messages.read`, `messages.edit`, `messages.delete`, `messages.react`, `conversations.join`, `conversations.leave`, `account.view_others`

Bots cannot create other bots and cannot delete accounts by default.

## Bot restrictions

The following permissions are never granted to bots, directly or through wildcards (`BOT_RESTRICTED_PERMISSIONS`):

- `bots.create`
- `bots.manage`
- `account.delete`
- `admin.users`
- `admin.servers`
- `admin.system`

`validate_permissions(permissions, is_bot=True)` rejects `*` or `messages.*`-style wildcards that would expand to a restricted permission.

## Usage notes

- User and bot tokens can have different effective permission grants.
- Wildcard grants such as `messages.*` and `*` expand access significantly. Use them only for trusted roles.
- Server-specific role systems can layer additional policy on top of these base permissions.

## Related pages

- [Authentication](./../api/authentication.md)
- [Security](../security.md)
