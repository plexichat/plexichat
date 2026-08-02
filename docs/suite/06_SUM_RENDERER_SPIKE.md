# PlexiSum renderer technical spike

Status: **decision made — implement with Univer; FortuneSheet is the fallback**

Last evaluated: 2026-08-02. Licenses and maintenance statuses were re-checked
before committing to a fork target. This document records the decision, the
license obligations, the adapter boundary, the theming approach, the benchmark
plan, and the explicit fork boundary.

## 1. Product constraint that drives the decision

PlexiSum's differentiator is not "Excel in a browser". It is:

> Collaborative, permission-aware spreadsheets on a **bounded viewport data
> model** where the authoritative data layer is ours and the browser never
> materializes the full workbook.

The renderer must therefore:

- render a visible range plus overscan from a **custom data source** (never a
  full materialized model)
- keep scrolling fluid at millions of rows
- capture edits as **range operations** for the operation log
- run formulas without blocking the UI thread
- be themeable to PlexiSuite tokens
- carry license terms that permit forking with attribution

## 2. Evaluation summary

| Criteria | Univer | FortuneSheet | Luckysheet |
| --- | --- | --- | --- |
| License | Apache-2.0 (open core; Univer Pro is commercial) | MIT | MIT |
| Maintenance | Actively maintained, v0.22+ heading to v1.0 (2026) | Active, v1.0.x series (late 2025 → 2026) | **Archived Oct 30, 2025** — excluded |
| Language/stack | TypeScript, Canvas rendering, Web Worker formulas | TypeScript, React/Vue + Immer | jQuery-era JavaScript (legacy) |
| Virtualization / large data | Dedicated Canvas engine, worker-based async formula engine — designed for massive grids | DOM/Immer grid — good for standard sheets, degrades at hundreds of thousands to millions of rows | Poor (legacy bottleneck) |
| Architecture | Plugin-first, isomorphic core, command/mutation pipeline, DI, Facade API (`FUniver`) | Component library, drop-in React/Vue | Monolithic |
| Extension path | Plugins + Facade API; forking core is possible but not the intended path | Forking the codebase directly is straightforward | n/a |
| Bundle control | Plugin/Preset modes allow tree-shaking | Moderate | Heavy |

**Decision: Univer** as the renderer adapter.

Rationale:

1. The million-row viewport thesis matches Univer's Canvas engine and async
   worker formula model directly; FortuneSheet's DOM/Immer model is precisely
   the architecture that degrades on the workloads PlexiSum targets.
2. The Facade API (`FUniver`) and command pipeline give us a clean adapter
   boundary: we can feed it a bounded data source and capture range operations
   without reaching into internals.
3. Apache-2.0 is permissive and fork-friendly with attribution; the open-core
   split only matters if we need Univer Pro features, which we deliberately
   avoid (CSV first, XLSX later, no pivot/commercial extras in v1).
4. Active maintenance with a v1.0 milestone de-risks a 2026 dependency.
5. Luckysheet is archived and excluded on maintenance grounds alone.

## 3. License and attribution obligations

- **Apache-2.0** requires: retain license/copyright notices, state changes if
  we distribute modified files, and include the NOTICE file if one exists.
  These obligations live in `THIRD_PARTY_NOTICES.md` at the client repo root
  and in the build output LICENSE/NOTICE assets.
- **Fork decision: do not fork the Univer core.** Extend via:
  - custom plugins (`@univerjs/plugin-*` patterns)
  - the Facade API (`FUniver`, `FWorkbook`, `FRange`)
  - a themed UI preset
  If a future need forces a core fork (for example a custom virtual data
  source that the Facade API cannot express), fork only the minimal rendering
  layer, keep the fork thin, and comply with Apache-2.0 attribution.
- **Fallback trigger:** if Univer integration proves intractable for our
  bounded-viewport data source (prototype benchmark stage), switch to
  FortuneSheet (MIT) and document why. MIT permits a direct fork with only
  copyright/notice retention.

## 4. Adapter boundary

The renderer is **never** the authoritative persistence model. Contract:

```
PlexiSum backend (ours)
        │  bounded viewport/range APIs
        ▼
PlexiSum adapter (ours)  ← owns revision, permission, operation capture
        │  FUniver Facade + plugin hooks
        ▼
Univer renderer (themed, non-authoritative)
```

Adapter responsibilities:

- **Data source**: implement the Univer data-provider surface against the
  bounded viewport API (`GET workbook/sheet/range?row_start&row_end&col_start&col_end&revision`)
  with overscan, staleness guard, and partial-load states. Never fetch a full
  column set for wide sheets.
- **Operation capture**: translate Univer command/mutation payloads into
  `sum_operations` records with idempotency key, base revision, actor, and
  bounded payload. Large pastes become compressed range operations.
- **Collaboration**: apply server operations into the renderer via the Facade
  API; resume from the last acknowledged revision after reconnect.
- **Permissions**: enforce workbook/sheet/range read-only and edit states at
  the adapter layer and re-verify server-side on every mutation endpoint. The
  client renderer state is never an authorization boundary.
- **Status surface**: expose connection state, revision, and calculation
  state to the workspace UI (loading, saving, connected, offline).

## 5. Theming

- Load Univer with its default UI preset, then override with a PlexiSuite
  theme object plus CSS custom properties mapped to the existing client
  tokens (`--bg-primary`, `--accent`, `--border`, `--text-*`, `--radius-*`).
- Deliver the theme as `src/css/suite/plexisum/theme.css` and an
  `applySumTheme()` helper so theme changes (classic/next/dark) can re-apply
  without re-mounting the renderer.
- Icons, fonts, and spacing follow the existing Phosphor + Inter conventions.

## 6. Benchmark plan

Benchmark before promoting PlexiSum beyond the spike. All runs are recorded
with environment (browser, device pixel ratio, hardware, network throttle).

Synthetic workloads:

- 1M rows × 20 columns dense numeric column
- 1M rows × 100 columns mixed types
- 500k rows with formulas referencing a distant column
- 100k-row × 4-column sheet with heavy formatting

Metrics:

| Metric | Target (desktop, baseline) |
| --- | --- |
| Initial viewport render latency | < 300 ms from mount to first interactive frame |
| Scroll responsiveness (60 Hz monitor) | sustained ≥ 55 fps at 1M rows |
| Overscan fetch latency (p50/p95) | < 80 ms / < 250 ms per viewport request |
| Paste latency (10k × 10 range) | < 250 ms end-to-end until local echo + queued op |
| Sort/filter on 1M-row column (server-indexed) | round trip < 500 ms; render < 200 ms |
| Memory (heap) after 5 min of pan/zoom at 1M rows | < 350 MB |
| Reconnect resume (after 5 s disconnect, 10k pending ops) | < 2 s to consistent state |
| Bundle cost (renderer + adapter, gzip) | < 1.2 MB loaded lazily on first Sum workspace |

Methodology notes:

- Use the same synthetic workbook generator for every candidate.
- Record p50/p95 from 30 repetitions, not single runs.
- Test both Chromium and Firefox (WebKit when available).
- Compare Univer vs FortuneSheet only if the prototype stage demands it; the
  default path is to benchmark Univer against our adapter and record absolute
  numbers, not a vendor bake-off.

## 7. Integration risks and mitigations

- **Bundle size**: load the renderer lazily on the first Sum workspace entry
  (dynamic import), use Univer plugin mode to tree-shake, and verify the gzip
  budget. `plexisum` is already `supportsEmbedded: false` in the registry so
  the renderer never loads inside chat.
- **Vite build**: Univer ships ESM; verify the Vite chunk graph, worker
  configuration (formula engine worker), and source maps. Pin the version.
- **Isomorphic/SSR**: the client is a static SPA; still guard renderer
  imports behind `typeof document !== 'undefined'` to avoid test/boot issues.
- **WebSocket worker fan-out**: never run full-workbook recalculation on a
  chat WebSocket worker. Calculations are client/worker-side for simple
  formulas and a dedicated calculation service for authoritative work.
- **Accessibility**: spreadsheet grids are inherently complex; document the
  keyboard-navigation baseline from Univer and ship a keyboard-first surface
  (cell focus, Enter editing, Escape, arrow navigation, `aria-label` on
  containers).

## 8. Data-layer position (unchanged by the spike)

- The SQLite-inspired logical model in `03_SUM_DATA_MODEL.md` stands.
- The repository abstraction stays database-neutral so SQLite (single node)
  and PostgreSQL (multi-worker) share the same domain contract.
- Idempotent operations, base/result revisions, snapshots, and conflict
  handling are server concerns; the renderer only consumes bounded projections.

## 9. Deliverables and acceptance criteria

| Deliverable | Acceptance |
| --- | --- |
| Renderer decision recorded | This document, signed off in review |
| License/attribution notices | `THIRD_PARTY_NOTICES.md` entry for Univer |
| Themed renderer prototype | Univer mounts in the Sum workspace with PlexiSuite theme on a bounded data source |
| Benchmark harness | Reproducible script + recorded results table for the metrics above |
| Operation capture adapter | Edit → `sum_operations` record with idempotency key and bounded payload |
| CSV import/export | Works through the adapter (XLSX is post-stable) |

Exit criteria for moving from spike to production implementation: benchmark
targets met, operation-capture adapter covered by tests, and a themed
prototype review passed.

## 10. Open questions

- Whether Univer's data-provider surface can fully express our column-segment
  (dense) reads without a fork; prototype first.
- Worker-based formula evaluation default settings and calculation-mode
  controls (manual vs auto per workbook).
- Whether to keep the CSV import path in the adapter or as a server-side
  pipeline; CSV import/export stays client-visible either way.
