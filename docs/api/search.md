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

## Response Shapes

### `GET /search/users?query=`

Stable envelope (canonical keys `items` + `total`):

```json
{
  "items": [
    {
      "id": "1234567890",
      "user_id": "1234567890",
      "username": "alice",
      "display_name": "Alice",
      "avatar_url": "/api/v1/avatars/users/1234567890",
      "badges": [],
      "score": 1.0
    }
  ],
  "total": 1,
  "results": [],
  "next_cursor": null
}
```

- `items` is the canonical result list; `results` is a legacy alias kept for
  backward compatibility and always mirrors `items`.
- `total` is the number of results in the current page (search is cursor
  paginated, so no global count is computed); use `next_cursor` to fetch more.
- Matching is case-insensitive over partial usernames; an exact email address
  also resolves to its account.

## Client Guidance

- debounce interactive search input
- paginate instead of repeatedly widening the same request
- avoid assuming every search index is globally visible or enabled

