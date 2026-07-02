# Channels Routes

## Purpose
Handles channel-level API endpoints for server channels and direct messages,
including invites, attachments, and channel updates.

## Structure

- `base.py` - `ChannelBase`: Base class with shared router and helpers
- `crud.py` - `ChannelCRUDMixin`: get_channel, update_channel, delete_channel
- `invites.py` - `ChannelInvitesMixin`: create_channel_invite, get_invite_info, join_server_via_invite, delete_invite
- `webhooks.py` - `ChannelWebhooksMixin`: get_channel_webhooks
- `attachments.py` - `ChannelAttachmentsMixin`: upload_attachment
- `ratchet.py` - `register_ratchet_routes`: GET /channels/{id}/ratchet (gated by the `channel_ratchet_encryption` license feature)
- `composer.py` - `ChannelsComposer`: Combines all mixins and exports `channels_router`
- `helpers.py` - `_channel_to_response`, `_get_upload_limit` utility functions

## Key Responsibilities
- Create, update, and fetch channels
- Manage channel invites and invite joins
- Handle attachment uploads for messages
- Resolve DM/Group DM participant metadata

## Main Entry Points
- GET /channels/{channel_id}
- PATCH /channels/{channel_id}
- DELETE /channels/{channel_id}
- GET /channels/{channel_id}/webhooks
- POST /channels/{channel_id}/invites
- POST /channels/{channel_id}/attachments
- GET /invites/{invite_code}
- POST /invites/{invite_code}
- DELETE /invites/{invite_code}

## Dependencies
- Core servers and messaging modules via the API registry
- Events module for dispatching channel-related events
- Channel and invite schemas for request/response validation

## Notes
- DM channel resolution uses user lookup helpers from users routes.
- Cached responses are applied for frequently accessed channel data.
- Routes are registered via `add_api_route()` in each mixin's `_register_routes()` method.