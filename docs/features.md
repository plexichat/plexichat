# Backend Feature Overview

This page summarizes the backend feature areas that are visible in the current codebase and documentation.

## Core Platform Areas

- **Authentication**: account registration, login, sessions, 2FA, password recovery, and bot authentication
- **Users**: profile reads and updates, avatars, notes, presence, and user settings
- **Relationships**: friend requests, blocks, and mutual relationship queries
- **Servers and channels**: guild-style organization, channel management, invites, and membership controls
- **Messaging**: message creation, edits, deletes, pins, replies, reactions, and read state
- **Search and discovery**: message search, user search, and public server search

## Real-Time Areas

- **WebSocket gateway**: heartbeats, identify/resume flow, dispatch events, and reconnect semantics
- **Presence and typing**: user presence updates and typing indicator signaling
- **Voice signaling**: ICE server discovery plus channel voice-connection metadata
- **Notifications**: unread feed plus read and read-all flows

## Content and Safety Areas

- **Media**: hash reporting, resumable uploads, upload-session management, and compression status
- **Reports and feedback**: user-submitted reports and feedback/status endpoints
- **Rate limiting**: global, user, IP, and route-level protections
- **Access gating**: optional API access-token enforcement discoverable through `/capabilities`

## Product Extensions

- **Polls**: message-attached polls with results and vote flows
- **Features and badges**: public feature visibility and admin-controlled tiers/badges
- **Webhooks**: inbound webhook execution and management
- **Versioning and capabilities**: runtime compatibility and capability discovery endpoints

## Notes

This page is intentionally descriptive rather than exhaustive. For route-group details, continue with the [API Reference](api/index.md) and the generated OpenAPI docs at `/docs`.
