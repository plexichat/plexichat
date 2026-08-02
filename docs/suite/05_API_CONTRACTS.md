# PlexiSuite API contract plan

These are planned contracts, not registered endpoints. They must be converted
into validated Pydantic schemas, authorization dependencies, tests, audit hooks,
and rate/size limits before implementation.

## Universal home

```text
GET /api/v1/suite/apps
GET /api/v1/suite/home/recent?limit=&cursor=
GET /api/v1/suite/home/pinned?limit=&cursor=
GET /api/v1/suite/home/shared?limit=&cursor=
GET /api/v1/suite/home/activity?limit=&cursor=
GET /api/v1/suite/search?q=&app_id=&cursor=
```

Responses are user-scoped and permission-filtered. Do not return inaccessible
metadata merely because the object exists.

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

The item route must distinguish metadata view, preview, download, edit, share,
move, delete, and version permissions. A download ticket must be short-lived,
opaque, revocable, and checked again when redeemed.

## Sum

```text
GET   /api/v1/suite/sum/workbooks
GET   /api/v1/suite/sum/workbooks/{workbook_id}/viewport
POST  /api/v1/suite/sum/workbooks/{workbook_id}/operations
GET   /api/v1/suite/sum/workbooks/{workbook_id}/operations?after_revision=
POST  /api/v1/suite/sum/workbooks/{workbook_id}/snapshots
```

Viewport requests and operation payloads must be bounded. Operations must be
idempotent, revisioned, actor-attributed, and permission-checked. Compressed
range operations are required for large paste actions.

## Future WebSocket boundary

A Sum realtime namespace may be added after the REST/repository contract is
stable. It must share the same operation envelope, sequencing, authorization,
rate limits, and cross-worker delivery guarantees as the existing artifact
fabric. Realtime delivery is not durable persistence.

## Required gates before registration

- route schemas and error contracts
- authentication and scope tests
- Stash ACL inheritance/revocation tests
- download-ticket tests
- Sum revision/idempotency/conflict tests
- operation size and rate limits
- audit and DSAR behavior
- migration and rollback plan
- self-test coverage
- load/viewport benchmarks
