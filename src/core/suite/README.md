# PlexiSuite backend domains

PlexiSuite is the family of client-side application experiences built on the
Plexichat core platform. Plexichat owns identity, authentication, sessions,
users, conversations, RBAC, artifact identity, media storage, notifications,
realtime transport, search, privacy, audit, and administration. The suite apps
own their product experiences on top of that shared foundation.

These domains register during normal startup. Migration `000` orchestrates their
schema creators, and the domain `setup()` hooks mount their routers. Capability
checks (`plexisum` / `plexistash`) gate both surfaces.

## Packages

### `registry`

Server-authoritative app manifests and universal-home capability projections.

### `stash`

Drive-like file and collection metadata, versions, grants, and
permission-checked download tickets.

### `sum`

PlexiSum workbooks. Workbook cell data lives in independent `.plexisum` SQLite
files stored through the media adapter; the main database holds only metadata,
ACL, snapshots, and the durable operation log. Includes a bounded server-side
formula evaluator.
