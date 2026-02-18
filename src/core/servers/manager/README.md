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

## Core Components
- ServerManager: main orchestration class for server operations
- AuditHandler: audit log creation and persistence
- ChannelHandler: channel lifecycle and updates
- RoleHandler: role creation, updates, and permissions
- MemberHandler: member joins, leaves, and role assignments

## Dependencies
- Core database cache helpers for hot-path lookups
- Server models, permissions, and exceptions from the servers package
- Messaging module for channel message hooks when available

## Data and Caching
- Uses in-memory caches for members, permissions, channels, and ownership
- TTL-based cache entries reduce repeated DB queries
- Cache invalidation occurs on membership and channel changes
