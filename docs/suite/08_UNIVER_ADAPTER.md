# PlexiSum Univer adapter — implementation status and fork decision

Status: **implemented without a fork.** Univer 0.25.x renders PlexiSum
workbooks through our adapter; a core fork remains a documented fallback
trigger, not a plan.

Last updated: 2026-08-10. This document records what was built, how the
custom data format stays ours, the protocol-mapping seams a fork would
touch, and why the fork decision from `06_SUM_RENDERER_SPIKE.md` is
resolved as "do not fork — the adapter suffices" for the current
requirements.

## 1. What changed since the renderer spike

The spike (`06_SUM_RENDERER_SPIKE.md`) chose Univer and sketched the
adapter boundary. That boundary is now implemented and covered by tests:

- `plexichat-client/src/js/suite/apps/plexisum/univer-renderer.js` mounts
  the engine through the `FUniver` facade
  (`@univerjs/core/facade` subpath) with a themed UI preset, a merged
  `enUS` locale bundle, a default workbook unit, and a bounded viewport
  seed — the renderer never materializes a full workbook.
- Edit capture goes through Univer command IDs (`set-range-values`,
  `clear-selection-content`, `insert/remove row/column`), translated into
  bounded range operations for the operation log.
- The workspace adapter (`workspace.js`) owns revision bookkeeping,
  permission-aware editing (read-only banners / protected ranges), and
  server-computed formulas after undo — all 409-resilient with an
  identity-based undo-stack restore.

Three integration blockers were found and fixed during implementation —
until then the engine had never actually mounted anywhere:

1. **Plugin normalization** — core's `registerPlugins` destructures every
   entry as a tuple, but the preset emits bare classes (`"e is not
   iterable"`). The adapter normalizes `preset.plugins` into tuples.
2. **`FUniver` import path** — it is exported from the
   `@univerjs/core/facade` subpath, not the package main entry.
3. **No locales and no workbook unit** — the preset ships no locale data
   and never creates a sheet. The adapter assembles a single `enUS`
   bundle from the six UI packages' own locale files (per-namespace deep
   merge, so a namespace collision can never silently drop strings) and
   creates the default workbook before rendering.

## 2. Adapter architecture (as built)

```
PlexiSum backend (ours)
        │  bounded viewport/range APIs + SUM opcodes (64–67)
        ▼
PlexiSum adapter (univer-renderer.js + workspace.js)  ← owns revision,
        │   permissions, operation capture, undo/redo
        │  FUniver Facade + plugin hooks
        ▼
Univer renderer (themed, non-authoritative)
```

- The authoritative data layer is the SUM data model
  (`03_SUM_DATA_MODEL.md`): workbooks, sheets, rows/columns, typed cells,
  operations, snapshots. The browser never holds the full workbook.
- The renderer's internal state is disposable — after reconnect or a
  conflict the adapter repaints from the last acknowledged revision.
- Client renderer state is never an authorization boundary; permissions
  are re-verified server-side on every mutation.

## 3. Custom data format — the format stays ours

- **Our format** is the SUM protocol: idempotent `sum_operations`
  envelopes (`operation_id`, `base_revision`, `result_revision`,
  `operation_type`, `cells`, `sheet_id`) carried over the WebSocket
  gateway as opcodes 64–67 (see `07_SUM_REALTIME.md`).
- **Univer's format** (its `WorkbookData` snapshot and command/mutation
  stream) is an internal detail. The adapter converts both ways at the
  boundary (`classifyValue` / `cellValue` / `rangeToCells` for reads,
  command-ID hooks for writes).
- Univer snapshots are **never persisted and never sent to the server**.
  Server storage and the operation log only ever see our envelopes, so
  swapping or forking the renderer later does not touch the data layer.

## 4. Protocol-mapping seams (what a fork would touch)

Univer's Command/Operation/Mutation pipeline maps near 1:1 onto the SUM
operation model:

| Univer command/mutation          | SUM operation           | Notes                                   |
| -------------------------------- | ----------------------- | --------------------------------------- |
| `set-range-values`               | range cell update       | typed values + formulas; large pastes become bounded range ops |
| `clear-selection-content`        | clear range             |                                         |
| `insert-row` / `insert-column`   | structural insert       | position + shift metadata               |
| `remove-row` / `remove-column`   | structural remove       |                                         |
| undo/redo command pipeline       | inverse / redo ops      | server-computed formulas propagate back into the renderer |
| formula engine (worker)          | `computed` on ops       | simple formulas client-side, authoritative computation server-side |

Today this mapping happens **outside** the engine — the adapter observes
the Facade's command results and emits our envelopes. A fork would only
become necessary if we must intercept the engine's internal command
stream *before* it reaches the UI, which no current feature requires.

## 5. Fork decision — resolved

- **Do not fork the Univer core.** The adapter path is implemented,
  tested, and license-clean (Apache-2.0; audit of the installed tree —
  349 packages, all permissive — recorded in
  `THIRD_PARTY_NOTICES.md`).
- **What a fork would buy:** protocol-native undo/redo (seam §4),
  collaborative cursors/selection broadcast, server-side formula
  injection inside the engine, and a custom virtual data source if the
  Facade API cannot express our column-segment dense reads.
- **What it costs:** upstream-merge burden and version churn, plus
  Apache-2.0 attribution obligations on any modified files we ship.
- **Trigger conditions** (fork only when one of these is real, not
  speculative):
  1. the Facade cannot express the bounded data source (the spike's open
     question — currently answered: the Facade path works),
  2. realtime/OT requirements outgrow adapter bridging,
  3. the spike §6 benchmark targets are unmet at 1M rows.
- Until then: extend via plugins and the Facade API. If a fork is ever
  triggered, keep it thin (minimal rendering layer), and record the
  attribution per spike §3.

## 6. Test coverage

- `tests/e2e/sum-univer.spec.cjs` — mount, edit, undo, and a two-client
  realtime merge (remote-delta / undo convergence).
- `tests/e2e/crypto-failclosed.spec.cjs` — cache-at-rest encryption is
  fail-closed and batched with a bounded concurrency pool
  (`ENCRYPT_CONCURRENCY = 32`). Measured on the real path (Chromium
  headless): 300-message batch 7 ms → 5 ms (1.51×), 2000-message batch
  36 ms → 30 ms (1.21×), with the PBKDF2 session key derived exactly once
  per `init()` — never re-derived per message.
- `suite-apps`, `suite`, `messages`, `app-shell` specs — regression
  coverage for the workspace and message-cache paths.

## 7. Outstanding items (not fork blockers)

- The spike §6 benchmark harness (1M-row viewport, scroll fps, overscan
  latency) — run and record before promoting PlexiSum beyond the current
  state.
- Formula-engine worker defaults (manual vs auto calculation mode).
- CSV import/export through the adapter (XLSX post-stable).
