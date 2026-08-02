# PlexiSuite migration and implementation strategy

## Current branch posture

The expansion branch contains contracts and product documentation only. It does
not register a router, add database migrations, alter startup, or replace the
existing artifact editor flows.

## Phase 1: client shell

1. Implement safe route parsing for app/object/server/channel context.
2. Implement the declarative app registry.
3. Add universal home inside the authenticated shell.
4. Add app switcher to the Plexichat brand control.
5. Add capability-aware app cards and quick-create actions.
6. Add bounded recent/pinned/shared/activity queries.
7. Add browser-history and refresh tests.

## Phase 2: promote existing editors

1. Give Scribe, Script, and Board independent homes.
2. Add templates, search, settings, onboarding, and empty states.
3. Add full workspace routes.
4. Keep existing globals as embedded adapters.
5. Preserve return-to-chat context.
6. Add denied/unavailable/offline states.

## Phase 3: Stash

1. Reuse existing media storage.
2. Finalize logical item, collection, version, grant, and audit contracts.
3. Add backend request/response schemas.
4. Add authorization tests before routes.
5. Add migrations with rollback only after schema review.
6. Add secure download tickets and preview isolation.
7. Add client home/workspace and chat save/share actions.
8. Add DSAR, retention, and audit coverage.

## Phase 4: Sum spike

1. Add the `sum` artifact capability contract without accepting production data.
2. Define bounded workbook/sheet/viewport/operation APIs.
3. Build a Univer prototype and a FortuneSheet prototype behind one adapter.
4. Generate realistic million-row benchmark data.
5. Measure memory, viewport latency, paste latency, filter/sort latency,
   reconnect behavior, and bundle size.
6. Select the renderer only after the spike.

## Phase 5: Sum production data layer

1. Implement repository contracts independent of SQLite/PostgreSQL.
2. Add workbook/sheet/column/row metadata.
3. Add typed sparse and/or dense segment storage based on benchmarks.
4. Add managed indexes and statistics.
5. Add idempotent operations, revisions, and snapshots.
6. Add permissions at workbook/sheet/range boundaries.
7. Add CSV import/export and later XLSX.
8. Add comments, mentions, notifications, and audit.

## Compatibility rules

- Existing artifact types and migrations remain unchanged initially.
- Unknown artifact types remain safely renderable as future/other.
- Existing routes keep their error contracts.
- Existing editor globals remain valid during migration.
- New app IDs are allow-listed; no dynamic module import from user input.
- No client route is an authorization boundary.
- No data migration happens merely to create the scaffold.

## Definition of done for an app feeling standalone

An app is not complete when its editor opens. It is complete when a user can:

1. enter through the universal home or a deep link,
2. see an app-specific home,
3. create or open an item,
4. understand loading/empty/denied/unavailable states,
5. search and return to recent/pinned/shared items,
6. change app settings,
7. share through Plexichat permissions,
8. return to the originating conversation when relevant, and
9. refresh or use browser history without losing safe context.
