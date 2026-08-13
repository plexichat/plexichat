# PlexiSuite API routes

Authenticated, capability-gated REST surface for the PlexiSuite expansion.
Routers are registered during application startup.

## Registry and universal home

```text
GET /api/v1/suite/apps
GET /api/v1/suite/home/recent
GET /api/v1/suite/home/pinned
GET /api/v1/suite/home/shared
GET /api/v1/suite/home/activity
GET /api/v1/suite/search?q=...
```

All responses are bounded, capability-aware, and permission-filtered. Partial
app failure does not break the universal home.

## Stash

```text
GET    /api/v1/suite/stash/items
POST   /api/v1/suite/stash/items
GET    /api/v1/suite/stash/items/{item_id}
PATCH  /api/v1/suite/stash/items/{item_id}
DELETE /api/v1/suite/stash/items/{item_id}
POST   /api/v1/suite/stash/items/{item_id}/shares
DELETE /api/v1/suite/stash/shares/{grant_id}
GET    /api/v1/suite/stash/items/{item_id}/versions
POST   /api/v1/suite/stash/items/{item_id}/download-ticket
```

Every metadata, preview, download, share, version, and mutation route performs
server-side authorization. Download tickets are short-lived and rechecked when
consumed.

## Sum

```text
GET    /api/v1/suite/sum/workbooks
POST   /api/v1/suite/sum/workbooks
POST   /api/v1/suite/sum/workbooks/from-template
GET    /api/v1/suite/sum/workbooks/{id}
PATCH  /api/v1/suite/sum/workbooks/{id}/pin
PATCH  /api/v1/suite/sum/workbooks/{id}/template
DELETE /api/v1/suite/sum/workbooks/{id}
POST   /api/v1/suite/sum/workbooks/{id}/restore
GET    /api/v1/suite/sum/workbooks/{id}/sheets/{sheet_id}/viewport
POST   /api/v1/suite/sum/workbooks/{id}/sheets/{sheet_id}/operations
POST   /api/v1/suite/sum/workbooks/{id}/sheets/{sheet_id}/sort
GET    /api/v1/suite/sum/workbooks/{id}/operations?after_revision=...
POST   /api/v1/suite/sum/workbooks/{id}/snapshots
GET    /api/v1/suite/sum/workbooks/{id}/snapshots
POST   /api/v1/suite/sum/workbooks/{id}/snapshots/{snapshot_id}/restore
POST   /api/v1/suite/sum/workbooks/{id}/sheets
PATCH  /api/v1/suite/sum/workbooks/{id}/sheets/{sheet_id}/rename
DELETE /api/v1/suite/sum/workbooks/{id}/sheets/{sheet_id}
GET    /api/v1/suite/sum/workbooks/{id}/file
POST   /api/v1/suite/sum/workbooks/{id}/file
GET    /api/v1/suite/sum/workbooks/{id}/sheets/{sheet_id}/csv
POST   /api/v1/suite/sum/workbooks/{id}/sheets/{sheet_id}/csv
GET    /api/v1/suite/sum/workbooks/{id}/xlsx
POST   /api/v1/suite/sum/workbooks/{id}/sheets/{sheet_id}/xlsx
GET    /api/v1/suite/sum/workbooks/{id}/grants
POST   /api/v1/suite/sum/workbooks/{id}/grants
DELETE /api/v1/suite/sum/grants/{grant_id}
GET    /api/v1/suite/sum/workbooks/{id}/sheets/{sheet_id}/range-permissions
POST   /api/v1/suite/sum/workbooks/{id}/sheets/{sheet_id}/range-permissions
DELETE /api/v1/suite/sum/workbooks/{id}/range-permissions/{range_id}
GET    /api/v1/suite/sum/workbooks/{id}/presence
PUT    /api/v1/suite/sum/workbooks/{id}/presence
GET    /api/v1/suite/sum/workbooks/{id}/sheets/{sheet_id}/freeze
PUT    /api/v1/suite/sum/workbooks/{id}/sheets/{sheet_id}/freeze
GET    /api/v1/suite/sum/workbooks/{id}/sheets/{sheet_id}/validations
POST   /api/v1/suite/sum/workbooks/{id}/sheets/{sheet_id}/validations
DELETE /api/v1/suite/sum/workbooks/{id}/sheets/{sheet_id}/validations/{validation_id}
GET    /api/v1/suite/sum/workbooks/{id}/sheets/{sheet_id}/conditional-formats
POST   /api/v1/suite/sum/workbooks/{id}/sheets/{sheet_id}/conditional-formats
DELETE /api/v1/suite/sum/workbooks/{id}/sheets/{sheet_id}/conditional-formats/{rule_id}
GET    /api/v1/suite/sum/workbooks/{id}/sheets/{sheet_id}/comments
POST   /api/v1/suite/sum/workbooks/{id}/sheets/{sheet_id}/comments
PATCH  /api/v1/suite/sum/workbooks/{id}/comments/{comment_id}
DELETE /api/v1/suite/sum/workbooks/{id}/comments/{comment_id}
```

The WebSocket fabric (gateway opcodes 64-67) carries live operation deltas and
subscription bootstrap, but durable writes always flow through the same
idempotent operation envelope and permission checks as REST. Large ranges use
bounded/compressed operations, never one request per cell.
