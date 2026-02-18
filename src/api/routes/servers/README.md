# Servers Routes

## Purpose
Exposes server and guild management endpoints, including channels, roles,
members, invites, bans, and automod rule management.

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

## Dependencies
- Core servers, presence, notifications, and automod modules
- Cached lookups for server and channel data
- Schemas for request/response validation

## Notes
- Uses cache invalidation helpers for server and channel updates.
- Channel responses normalize channel type values for API consumers.
