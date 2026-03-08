# Search Routes

The backend exposes search endpoints for messages, users, and public servers.

## Routes

- `GET /search/messages`
- `GET /search/users`
- `GET /search/servers`

## Purpose

- message search over accessible conversation history
- user search for discovery and lookup flows
- public server discovery for server browsing experiences

## Expected Behavior

- authentication may be required depending on the route and server policy
- results are typically paginated or bounded rather than unbounded scans
- access control still applies; search does not bypass normal visibility rules

## Client Guidance

- debounce interactive search input
- paginate instead of repeatedly widening the same request
- avoid assuming every search index is globally visible or enabled

