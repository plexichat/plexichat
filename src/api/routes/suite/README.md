# PlexiSuite API routes

## Overview

PlexiSuite is the authenticated, capability-gated REST surface for the Stash and Sum expansions. You use it to browse the app catalog, query the universal home feeds, and manage Stash items and Sum workbooks. All routes require a valid `Authorization` token and respect per-domain capability checks, permission filters, and bounded pagination. Partial app failure does not break the universal home.

Routers live in `registry.py` (catalog and home), `stash.py` (PlexiStash), and `sum.py` (PlexiSum). They are registered under the `/suite` prefix by the parent API router.

## Registry and universal home

Base path: `/api/v1/suite`.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/suite/apps` | List the capability-aware app catalog |
| `GET` | `/suite/home/recent?limit=` | List recent personal items (artifacts, Stash, and Sum) |
| `GET` | `/suite/home/pinned?limit=` | List pinned Stash items |
| `GET` | `/suite/home/shared?limit=` | List server-scoped shared items (Stash + Sum + authorized artifacts) |
| `GET` | `/suite/home/activity?limit=` | List your audit activity from `suite_audit_log` |
| `GET` | `/suite/search?q=&limit=` | Search visible metadata across all domains |

Query parameters:

* `limit` — `1–50` (default `24`) for all home and search endpoints.
* `q` — `1–200` characters, required for `/search`.

All responses are capability-aware and permission-filtered. You only see apps whose capability state is `available`. Shared feeds check `artifact.view` per server and merge results in a bounded sort.

### Example

```python
GET /api/v1/suite/apps
# -> {"schemaVersion": 1, "apps": [{"id": "plexistash", ...}]}

GET /api/v1/suite/home/recent?limit=24
# -> {"schemaVersion": 1, "items": [...], "total": 12, "hasMore": false}
```

## Stash

Base path: `/api/v1/suite/stash`. PlexiStash is gated by the `plexistash` capability.

### Item CRUD and search

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/items?limit=&offset=&search=&scope=&server_id=&collection_id=&pinned=` | List items visible to you |
| `POST` | `/items` | Create an item (no filesystem path is accepted) |
| `GET` | `/items/{item_id}` | Get a single item |
| `PATCH` | `/items/{item_id}` | Update an item |
| `DELETE` | `/items/{item_id}` | Soft-delete an item (204) |
| `POST` | `/items/{item_id}/restore` | Restore a soft-deleted item |
| `POST` | `/upload` | Upload file bytes as base64 and create a file item |

List parameters: `limit` `1–100` (default `50`), `offset` `>= 0`, `search` `<= 200`, `scope` `personal` or `server`, `pinned` boolean. Uploads enforce `MAX_UPLOAD_ENCODED_LEN = 70_000_000` and `MAX_UPLOAD_DECODED_BYTES = 50 * 1024 * 1024` before reaching the media store.

### Versions

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/items/{item_id}/versions?limit=` | List versions |
| `POST` | `/items/{item_id}/versions` | Create a version |
| `GET` | `/items/{item_id}/versions/diff?a=&b=` | Diff two versions |

### Sharing and download tickets

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/items/{item_id}/grants` | Grant access to a user |
| `DELETE` | `/grants/{grant_id}` | Revoke a grant |
| `POST` | `/collections/{collection_id}/grants` | Grant access to a collection |
| `GET` | `/collections/{collection_id}/grants` | List collection grants |
| `DELETE` | `/collection-grants/{grant_id}` | Revoke a collection grant |
| `POST` | `/items/{item_id}/download-ticket?version_id=&ttl_seconds=` | Create a short-lived ticket (`ttl_seconds` `1–300`, default `300`) |
| `POST` | `/download-ticket/redeem` | Redeem a ticket once; rechecks authorization at use time |
| `POST` | `/items/{item_id}/share-links` | Create an opaque share link (optional password) |
| `GET` | `/items/{item_id}/share-links` | List share links |
| `DELETE` | `/share-links/{link_id}` | Revoke a share link |
| `POST` | `/share-links/resolve` | Resolve a share link token |
| `POST` | `/share-links/download` | Issue a download ticket for a share-link recipient |

Every metadata, preview, download, share, version, and mutation route performs server-side authorization. Download tickets are short-lived and rechecked when consumed.

### Collections

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/collections?server_id=` | List collections |
| `POST` | `/collections` | Create a collection |

## Sum

Base path: `/api/v1/suite/sum`. PlexiSum is gated by the `plexisum` capability.

### Workbook lifecycle

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/workbooks?limit=&offset=&template=&search=` | List workbooks |
| `POST` | `/workbooks` | Create a workbook |
| `POST` | `/workbooks/from-template` | Create a workbook from a template |
| `GET` | `/workbooks/{id}` | Get a workbook |
| `PATCH` | `/workbooks/{id}/pin` | Pin or unpin a workbook |
| `PATCH` | `/workbooks/{id}/template` | Mark or unmark a workbook as a template |
| `DELETE` | `/workbooks/{id}` | Delete a workbook (204) |
| `POST` | `/workbooks/{id}/restore` | Restore a deleted workbook |

### Sheets and operations

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/workbooks/{id}/sheets` | Create a sheet |
| `PATCH` | `/workbooks/{id}/sheets/{sheet_id}/rename` | Rename a sheet |
| `DELETE` | `/workbooks/{id}/sheets/{sheet_id}` | Delete a sheet |
| `GET` | `/workbooks/{id}/sheets/{sheet_id}/viewport?row_start=&row_end=&column_start=&column_end=&filters=` | Read a bounded cell viewport |
| `POST` | `/workbooks/{id}/sheets/{sheet_id}/operations` | Apply a cell operation (idempotent envelope) |
| `POST` | `/workbooks/{id}/sheets/{sheet_id}/sort` | Stable-sort a row range by computed values |
| `GET` | `/workbooks/{id}/operations?after_revision=&limit=` | Replay operations after a revision |

Large ranges use bounded and compressed operations. Do not send one request per cell.

### Snapshots

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/workbooks/{id}/snapshots` | Create a snapshot |
| `GET` | `/workbooks/{id}/snapshots?limit=` | List snapshots |
| `POST` | `/workbooks/{id}/snapshots/{snapshot_id}/restore` | Restore a snapshot |

### Import and export

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/workbooks/{id}/file` | Download the `.plexisum` file |
| `POST` | `/workbooks/{id}/file` | Replace the file (base64, guarded by `base_revision`) |
| `GET` | `/workbooks/{id}/sheets/{sheet_id}/csv` | Export a sheet as CSV |
| `POST` | `/workbooks/{id}/sheets/{sheet_id}/csv` | Import CSV into a sheet |

### Sharing and permissions

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/workbooks/{id}/grants` | List workbook grants |
| `POST` | `/workbooks/{id}/grants` | Grant access to a workbook |
| `DELETE` | `/grants/{grant_id}` | Revoke a workbook grant |
| `GET` | `/workbooks/{id}/sheets/{sheet_id}/range-permissions` | List range permissions |
| `POST` | `/workbooks/{id}/sheets/{sheet_id}/range-permissions` | Set a range permission |
| `DELETE` | `/workbooks/{id}/range-permissions/{range_id}` | Remove a range permission |

### Presence and view state

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/workbooks/{id}/presence` | List current viewers |
| `PUT` | `/workbooks/{id}/presence` | Heartbeat presence |
| `GET` | `/workbooks/{id}/sheets/{sheet_id}/freeze` | Get freeze panes |
| `PUT` | `/workbooks/{id}/sheets/{sheet_id}/freeze` | Set freeze panes |

### Data validation and formatting

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/workbooks/{id}/sheets/{sheet_id}/validations` | List validations |
| `POST` | `/workbooks/{id}/sheets/{sheet_id}/validations` | Create a validation rule |
| `DELETE` | `/workbooks/{id}/sheets/{sheet_id}/validations/{validation_id}` | Delete a validation rule |
| `GET` | `/workbooks/{id}/sheets/{sheet_id}/conditional-formats` | List conditional formats |
| `POST` | `/workbooks/{id}/sheets/{sheet_id}/conditional-formats` | Create a conditional format |
| `DELETE` | `/workbooks/{id}/sheets/{sheet_id}/conditional-formats/{rule_id}` | Delete a conditional format |

### Cell comments

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/workbooks/{id}/sheets/{sheet_id}/comments?row=&column=` | List comments (optionally filtered by cell) |
| `POST` | `/workbooks/{id}/sheets/{sheet_id}/comments` | Add a comment to a cell |
| `PATCH` | `/workbooks/{id}/comments/{comment_id}?resolved=` | Resolve or unresolve a comment |
| `DELETE` | `/workbooks/{id}/comments/{comment_id}` | Delete a comment |

## How you deploy and extend this package

1. Add a new handler in `stash.py` or `sum.py` and annotate it with `APIRouter` metadata (`prefix`, `tags`, `responses`).
2. Add the corresponding request and response schemas in `src/api/schemas/stash.py` or `src/api/schemas/sum.py`.
3. Gate the route with `_require_stash_capability()` or `_require_sum_capability()` when it touches licensed state.
4. Keep pagination bounded (`limit` and `offset`) and recheck authorization inside the manager call, not only in the router.

## Notes

* The WebSocket fabric (gateway opcodes `60–67` and dedicated presence opcodes) carries live operation deltas and subscription bootstrap, but durable writes always flow through the same idempotent operation envelope and permission checks as REST.
* Stash media URLs are signed through `media.sign_url()` and bound to the ticket's remaining lifetime so a redeemed ticket cannot outlive its own expiry.
* The suite home feeds intentionally exclude non-suite artifact types (voice calls, uploads, raw files, transcripts) so the universal launcher does not surface dead cards.
