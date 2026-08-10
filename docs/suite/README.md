# PlexiSuite expansion

PlexiSuite is a family of client-side application experiences built on the
Plexichat core platform.

## Core principle

Plexichat owns:

- identity, authentication, sessions, and users
- conversations, channels, servers, and messaging
- RBAC, sharing, and authorization
- artifact identity and lifecycle
- media/binary storage
- notifications and realtime transport
- search infrastructure, privacy, audit, and administration

PlexiScribe, PlexiScript, PlexiBoard, PlexiStash, and PlexiSum own their
product experiences, workflows, and domain-specific client behavior. They do
not duplicate auth, storage, or permission systems.

## Standalone meaning

An app is standalone when it has its own home, navigation, creation flow,
recent/pinned/shared views, empty states, templates, search, settings,
onboarding, and deep links. It does not need a separate deployment or backend.

## Documents

- [Architecture and app homes](01_ARCHITECTURE.md)
- [PlexiStash RBAC and storage](02_STASH_RBAC.md)
- [PlexiSum data model and performance](03_SUM_DATA_MODEL.md)
- [Migration and implementation strategy](04_MIGRATION_STRATEGY.md)
- [API contract plan](05_API_CONTRACTS.md)
- [PlexiSum real-time fabric](07_SUM_REALTIME.md)

## Implementation status

PlexiSuite is no longer a contract-only scaffold. The Stash and Sum domains are
fully registered during normal startup: migration `000` orchestrates the domain
schema creators, the domain `setup()` hooks mount their routers, capability
checks (`plexisum`, `plexistash`) gate both surfaces, and PlexiSum cell data
lives in `.plexisum` SQLite workbook files stored through the configured media
adapter (never in the main database).

- **PlexiStash** — metadata/ACL backend (items, collections, versions, grants,
  download tickets) plus a client workspace app mounted inside the suite
  launcher.
- **PlexiSum** — full server data layer (19 tables, ~40 REST endpoints,
  bounded formula engine) with a bounded-viewport grid client, now including a
  real-time gateway fabric (opcodes 64-67) for collaborative editing.
- **Renderer** — the **Univer** engine (Apache-2.0, `@univerjs/preset-sheets-core`,
  pinned 0.25.1, lazy-loaded and code-split) is now the **default** renderer:
  the workspace auto-mounts it after the first viewport seeds data, with the
  hand-rolled bounded grid as both the persisted opt-out (`plexisum.renderer`
  = `'grid'` in localStorage) and the automatic fallback on mount failure.
  Both renderers share the same op/revision contract; undo/redo is
  server-backed in both (the workspace's stack persists the inverse op, and
  Univer's native undo/redo toolbar buttons are hidden while capture-phase
  Ctrl+Z/Ctrl+Shift+Z/Ctrl+Y interception keeps the engine's internal history
  from diverging from the server). Per the renderer spike, Univer is never
  the persistence authority: it renders a bounded viewport, captures edits as
  idempotent range operations, and the server (including formula evaluation)
  stays authoritative. License audit: the `@univerjs-pro/*` commercial
  packages are excluded by avoiding `@univerjs/presets` and the collaboration
  presets (see `plexichat-client/THIRD_PARTY_NOTICES.md`).
- **Write path** — cell edits are coalesced: acknowledged durably via the
  main-database `sum_operations` buffer + revision CAS and applied to a live
  in-memory copy, with the immutable blob promoted only when the flush window
  (25 ops / 2 s) is crossed. Crash recovery replays the buffer over the last
  blob; `blob_revision` tracks the materialized revision.
