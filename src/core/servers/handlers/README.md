# Server handlers

This directory contains specialized handlers that encapsulate server-domain logic for `ServerManager`. Each handler owns a single responsibility and delegates persistence, caching, and audit logging through the manager instance passed to its constructor.

## Overview

`ServerManager` instantiates its handlers in `src/core/servers/manager/base.py:139` and exposes them as attributes. Use the manager's public methods rather than calling handlers directly unless you need handler-specific behavior.

| Handler | File | Responsibility |
|---|---|---|
| `AuditHandler` | `audit_handler.py` | Write and query audit log entries. |
| `ChannelHandler` | `channel_handler.py` | Validate and manage channels and categories. |
| `RoleHandler` | `role_handler.py` | Create, update, delete, and assign roles; manage permission overrides. |
| `MemberHandler` | `member_handler.py` | Manage members, bans, and invites. |

All handlers share the same constructor signature:

```python
handler = SomeHandler(manager)  # manager is a ServerManager instance
self.db = manager._db           # handlers reuse the manager's database handle
```

## AuditHandler

Defined in `audit_handler.py:14`.

### Methods

| Method | Description |
|---|---|
| `log_audit(server_id, user_id, action, target_type, target_id, changes, reason)` | Insert an audit entry. Encrypts `changes` with `utils.encryption.encrypt_data` when available. See `audit_handler.py:19`. |
| `get_audit_log(server_id, limit, action_type, before_id)` | Return up to `min(limit, 100)` entries ordered by `created_at DESC`. Supports filtering by `AuditLogAction` and pagination with `before_id`. See `audit_handler.py:63`. |

Changes are stored as both plaintext JSON (`changes`) and an encrypted copy (`changes_encrypted`) for audit integrity.

## ChannelHandler

Defined in `channel_handler.py:24`.

### Methods

| Method | Description |
|---|---|
| `validate_channel_name(name, channel_type)` | Normalize and validate the name. Text and announcement channels are lowercased and hyphenated; other types preserve spacing. Enforces `channel_name_max_length` from manager config. See `channel_handler.py:29`. |
| `create_channel(user_id, server_id, name, channel_type, category_id, topic, nsfw, slowmode_seconds, read_receipts_enabled)` | Create a channel after membership and `channels.manage` checks. Creates a messaging conversation for text channels and rolls it back on failure. See `channel_handler.py:61`. |
| `create_category(user_id, server_id, name)` | Create a channel category. See `channel_handler.py:192`. |
| `delete_category(user_id, category_id)` | Delete a category and null out `category_id` on member channels. See `channel_handler.py:222`. |
| `get_channels(user_id, server_id, channel_type)` | Return channels the caller can view. Cached with `@cached(ttl=30, prefix="server_channels")` and batch-filters by `channels.view`. See `channel_handler.py:243`. |
| `update_channel(user_id, channel_id, name, topic, nsfw, slowmode_seconds, read_receipts_enabled, category_id)` | Update channel fields. Validates `slowmode_seconds` in `0..21600`. See `channel_handler.py:285`. |
| `delete_channel(user_id, channel_id)` | Soft-delete a channel, cascade-delete threads, remove permission overrides, and tombstone the linked conversation. See `channel_handler.py:400`. |
| `move_channel(user_id, channel_id, position)` | Update channel position. See `channel_handler.py:470`. |

Permission requirement: all mutating operations require `channels.manage` on the server or channel.

## RoleHandler

Defined in `role_handler.py:30`.

### Methods

| Method | Description |
|---|---|
| `validate_role_name(name)` | Strip and enforce `role_name_max_length`. See `role_handler.py:35`. |
| `create_role(user_id, server_id, name, permissions, color, hoist, mentionable)` | Create a role. Validates the permission dict, checks grantability, enforces `max_roles_per_server`, and clamps position below the creator's highest role. See `role_handler.py:48`. |
| `get_role(role_id, user_id)` | Return a role if the caller is a member of the server. See `role_handler.py:130`. |
| `get_roles(user_id, server_id)` | Return all roles ordered by `position DESC`. Requires `roles.manage`. See `role_handler.py:142`. |
| `update_role(user_id, role_id, name, permissions, color, hoist, mentionable, position)` | Update role fields with hierarchy and permission-grantability checks. Invalidates server permission caches. See `role_handler.py:151`. |
| `delete_role(user_id, role_id)` | Delete a role and its member assignments. Rejects the default role. See `role_handler.py:280`. |
| `move_role(user_id, role_id, position)` | Move a role in the hierarchy. Rejects the default role and positions at or above the actor's highest role. See `role_handler.py:312`. |
| `get_channel_override / set_channel_override / delete_channel_override` | Manage per-channel allow/deny overrides for a role or member. Invalidates `perms:*` and `has_perm:*` caches. See `role_handler.py:345`. |
| `get_permissions / get_permissions_batch / has_permission` | Resolve effective permissions with base-role calculation, channel overrides, and timeout restrictions. `get_permissions` is two-level cached (in-memory + Valkey). See `role_handler.py:562`. |
| `get_member_roles / assign_role / remove_role / assign_roles_from_whitelist` | Manage member-role assignments with hierarchy and administrator-escalation guards. See `role_handler.py:703`. |

Key invariants:

* You cannot modify, delete, or move the `@everyone` default role.
* You cannot grant permissions you do not possess.
* Role assignment with `administrator` is rejected in self-service flows (`assign_roles_from_whitelist`).

## MemberHandler

Defined in `member_handler.py`.

Manages membership lifecycle, bans, and invites. The manager exposes these operations directly; see `src/core/servers/manager/base.py:478` for the delegating methods (`add_member`, `get_member`, `update_member`, `kick_member`, `ban_member`, `unban_member`, `get_bans`, `create_invite`, `get_invite`, `use_invite`, `delete_invite`).

## Related code

* `src/core/servers/manager/base.py` — `ServerManager` composition and handler instantiation.
* `src/core/servers/manager/converters.py` — Row-to-model conversion helpers (`_row_to_channel`, `_row_to_role`, `_row_to_override`, `_row_to_audit_entry`) used by all handlers.
* `src/core/servers/models.py` — `Channel`, `ChannelCategory`, `Role`, `ChannelOverride`, `AuditLogEntry` definitions.
* `src/core/servers/exceptions.py` — Domain exceptions raised by handlers.
* `src/core/servers/permission_utils.py` — `calculate_base_permissions`, `apply_channel_overrides`, `can_manage_role` logic used by `RoleHandler`.
