# Users Routes

## Purpose
Provides user profile and account endpoints, including profile updates,
avatar management, DM creation, and user settings.

## Key Responsibilities
- Fetch user profiles and public user data
- Update profile details and avatars
- Create or resolve DM and notes channels
- Manage user messaging settings
- Provide cache-backed user lookups for other routes

## Main Entry Points
- GET /users/@me
- PATCH /users/@me
- POST /users/@me/avatar
- POST /users/@me/channels
- GET /users/{user_id}
- GET /users/{user_id}/mutual-servers
- GET /users/@me/settings/messaging

## Dependencies
- Core auth and settings modules via the API registry
- Cached helpers for common user lookups
- Schemas for user, channel, and messaging responses

## Notes
- Public and private user responses share a conversion helper.
- Cache invalidation is applied after profile updates.
