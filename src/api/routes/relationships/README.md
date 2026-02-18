# Relationships Routes

## Purpose
Manages social graph endpoints for friends, blocks, and relationship updates.

## Key Responsibilities
- Send, accept, and remove friend requests
- Block and unblock users
- List relationships and friendship status
- Coordinate notifications for relationship changes

## Main Entry Points
- GET /relationships
- POST /relationships
- DELETE /relationships/{user_id}
- POST /relationships/{user_id}/block
- DELETE /relationships/{user_id}/block

## Dependencies
- Core relationships module via the API registry
- User schema responses for relationship payloads
- Auth middleware for current-user resolution

## Notes
- Relationships are normalized to consistent response types.
- Block checks are used by other modules to filter visibility.
