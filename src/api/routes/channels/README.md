# Channels Routes

## Purpose
Handles channel-level API endpoints for server channels and direct messages,
including invites, attachments, and channel updates.

## Key Responsibilities
- Create, update, and fetch channels
- Manage channel invites and invite joins
- Handle attachment uploads for messages
- Resolve DM/Group DM participant metadata

## Main Entry Points
- POST /channels
- PATCH /channels/{channel_id}
- GET /channels/{channel_id}
- POST /channels/{channel_id}/invites
- POST /invites/{invite_code}/join
- POST /channels/{channel_id}/attachments

## Dependencies
- Core servers and messaging modules via the API registry
- Events module for dispatching channel-related events
- Channel and invite schemas for request/response validation

## Notes
- DM channel resolution uses user lookup helpers from users routes.
- Cached responses are applied for frequently accessed channel data.
