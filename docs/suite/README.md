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
  bounded formula engine) with a bounded-viewport client (rendered through the
  Univer engine), now including a real-time gateway fabric (opcodes 64-67) for
  collaborative editing.
- **Renderer** — the **Univer** engine (Apache-2.0, `@univerjs/preset-sheets-core`,
  pinned 0.25.1, lazy-loaded and code-split) is the **only** renderer: the
  workspace auto-mounts it after the first viewport seeds data, and the
  hand-rolled bounded grid was removed (if mounting throws, the workspace
  surfaces the error). Undo/redo is server-backed (the workspace's stack
  persists the inverse op, and Univer's native undo/redo toolbar buttons are
  hidden while capture-phase Ctrl+Z/Ctrl+Shift+Z/Ctrl+Y interception keeps
  the engine's internal history from diverging from the server). Univer is
  never the persistence authority: it renders a bounded viewport and captures
  edits as idempotent range operations. Formulas are computed client-side by
  the workspace's `ClientFormulaRuntime`; the server's bounded evaluator
  recomputes on read as the durable authority. License audit: the
  `@univerjs-pro/*` commercial packages are excluded by avoiding
  `@univerjs/presets` and the collaboration presets (see
  `plexichat-client/THIRD_PARTY_NOTICES.md`).
- **Write path** — cell edits are coalesced: acknowledged durably via the
  main-database `sum_operations` buffer + revision CAS and applied to a live
  in-memory copy, with the immutable blob promoted only when the flush window
  (25 ops / 2 s) is crossed. Crash recovery replays the buffer over the last
  blob; `blob_revision` tracks the materialized revision.
- **Client write-ahead log** — every queued edit is persisted to an IndexedDB
  WAL (`shared/client-wal.js`) before it is sent and removed only on server
  ack. Pending ops are replayed idempotently (with rebase-on-conflict) on
  workspace mount and on gateway reconnect, so edits survive page reloads and
  brief outages.
- **Charts / pivot / sparklines** — the workspace surfaces the previously
  unwired client modules: an SVG chart panel, a pivot-table summarizer, and
  sparkline previews, all driven by the current selection.
- **Templates** — workbooks can be marked/unmarked as templates in-place
  (`PATCH /suite/sum/workbooks/{id}/template`; client "Save as template"), not
  only created as empty templates.
- **Settings & onboarding** — a PlexiSum settings section (render-quality/
  pixel-ratio preference) and a first-run onboarding empty state with a create
  CTA are wired in.
- **Search** — `/suite/search` performs server-side Sum title search (SQL
  `LIKE`) rather than filtering a bounded in-memory list, so matches beyond the
  first page are found.
- **Realtime efficiency** — the workspace skips redundant full viewport
  reloads: a `WORKBOOK_UPDATE` at a revision already applied via a `SUM_OP`
  delta is ignored, and the 15 s poll fallback only runs while the gateway is
  disconnected.
