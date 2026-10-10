# PlexiSuite

PlexiSuite is a set of client-side apps that run on the Plexichat core platform. The core platform owns shared services; each Suite app owns its own product experience.

## What each layer owns

Plexichat core owns:

*   Identity, authentication, sessions, and users.
*   Conversations, channels, servers, and messaging.
*   Role-based access control, sharing, and authorization.
*   Artifact identity and lifecycle.
*   Media and binary storage.
*   Notifications and real-time transport.
*   Search infrastructure, privacy, audit, and administration.

PlexiScribe, PlexiScript, PlexiBoard, PlexiStash, and PlexiSum own product workflows and client behavior. They do not reimplement authentication, storage, or permissions.

## When an app is standalone

An app is standalone when it provides its own home, navigation, creation flow, recent and pinned and shared views, empty states, templates, search, settings, onboarding, and deep links. A standalone app does not require a separate deployment or backend.

## Implementation status

PlexiSuite is no longer a contract-only scaffold. The Stash and Sum domains are registered during normal startup.

*   **Migration** – Migration `000` orchestrates the domain schema creators.
*   **Routing** – Each domain `setup()` hook mounts its routers.
*   **Capabilities** – Capability checks (`plexisum`, `plexistash`) gate both server and client surfaces.
*   **Storage** – PlexiSum cell data lives in `.plexisum` SQLite workbook files stored through the configured media adapter, not in the main database.

### PlexiStash

PlexiStash provides the metadata and access-control backend for items, collections, versions, grants, and download tickets, plus a client workspace mounted inside the suite launcher.

### PlexiSum

PlexiSum provides the server data layer (19 tables, about 40 REST endpoints, and a bounded formula engine) and a real-time gateway fabric using opcodes `64`–`67` for collaborative editing. See [Gateway opcodes](../websocket/opcodes.md#real-time-fabric-opcodes).

Key behaviors:

*   **Renderer** – The Univer engine (Apache-2.0, `@univerjs/preset-sheets-core` at `0.25.1`, lazy-loaded and code-split) is the only renderer. The workspace mounts Univer after the first viewport seeds data. A hand-rolled bounded grid is not used. If mounting fails, the workspace shows the error. Undo and redo are server-backed: the workspace persists the inverse operation, Univer toolbar undo and redo are hidden, and capture-phase interception of `Ctrl+Z`, `Ctrl+Shift+Z`, and `Ctrl+Y` prevents Univer history from diverging from the server. Univer never owns persistence; it renders a bounded viewport and captures edits as idempotent range operations. Formulas run client-side in `ClientFormulaRuntime`; the server recomputes on read as the durable authority. Third-party notices are in `plexichat-client/THIRD_PARTY_NOTICES.md`.
*   **Write path** – Cell edits are coalesced. The server acknowledges edits durably in the `sum_operations` buffer with revision compare-and-swap, applies them to a live in-memory copy, and promotes the immutable blob when the flush window (25 operations or 2 seconds) is crossed. Crash recovery replays the buffer over the last blob. `blob_revision` tracks the materialized revision.
*   **Client write-ahead log** – Each queued edit is persisted to an IndexedDB WAL (`shared/client-wal.js`) before sending and removed only on server acknowledgment. Pending operations are replayed idempotently with rebase-on-conflict on workspace mount and on gateway reconnect, so edits survive page reloads and brief outages.
*   **Charts, pivot, and sparklines** – The workspace includes an SVG chart panel, a pivot-table summarizer, and sparkline previews driven by the current selection.
*   **Templates** – You can mark or unmark a workbook as a template in place with `PATCH /suite/sum/workbooks/{id}/template`.
*   **Settings and onboarding** – The settings section includes a render-quality and pixel-ratio preference. The first-run empty state includes a create call-to-action.
*   **Search** – `GET /suite/search` runs a server-side SQL `LIKE` search on Sum titles, so matches beyond the first page are returned.
*   **Real-time efficiency** – The workspace skips redundant viewport reloads. A `WORKBOOK_UPDATE` at a revision already applied through a `SUM_OP` delta is ignored, and the 15-second poll fallback runs only while the gateway is disconnected.
