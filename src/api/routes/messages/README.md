# Messages Routes

## Purpose
Exposes messaging endpoints for sending, editing, deleting, and fetching
messages across channels and conversations.

## Key Responsibilities
- Send and edit messages
- Fetch message history and single-message lookups
- Manage reactions and attachments metadata
- Enforce message permissions and visibility rules

## Main Entry Points
- POST /messages
- PATCH /messages/{message_id}
- DELETE /messages/{message_id}
- GET /messages/{conversation_id}
- GET /messages/{conversation_id}/pinned

## Dependencies
- Core messaging module via the API registry
- Notifications and reactions modules for side effects
- Schemas for message and attachment payloads

## Notes
- Routes use consistent error response shapes.
- Message payloads map to conversation and channel contexts.
