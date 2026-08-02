# Planned PlexiSuite API routes

This directory reserves API boundaries for the expansion. It intentionally does
not register routers or change the current application startup.

## Registry and universal home

```text
GET /api/v1/suite/apps
GET /api/v1/suite/home/recent
GET /api/v1/suite/home/pinned
GET /api/v1/suite/home/shared
GET /api/v1/suite/home/activity
GET /api/v1/suite/search?q=...
```

All responses must be bounded, capability-aware, and permission-filtered.
Partial app failure should not break the universal home.

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

Every metadata, preview, download, share, version, and mutation route must
perform server-side authorization. A download ticket is short-lived and
rechecked when consumed.

## Sum

```text
GET   /api/v1/suite/sum/workbooks
GET   /api/v1/suite/sum/workbooks/{workbook_id}/viewport
POST  /api/v1/suite/sum/workbooks/{workbook_id}/operations
GET   /api/v1/suite/sum/workbooks/{workbook_id}/operations?after_revision=...
POST  /api/v1/suite/sum/workbooks/{workbook_id}/snapshots
```

A future WebSocket namespace may carry live operations, but durable writes
must use the same idempotent operation envelope and permission checks as REST.
Large ranges use bounded/compressed operations, never one request per cell.

## Registration gate

Before implementing routes, add:

- request/response schemas
- auth and scope tests
- RBAC tests
- rate and size limits
- audit events
- migration/rollback plan
- self-test coverage

Only then should a router be included in the application.
