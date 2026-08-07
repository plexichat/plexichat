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
- **Remaining work** — the PlexiSum renderer is a hand-rolled grid; the spike
  decision to replace it with a Univer-based renderer (behind the same
  op/revision contract) is a future fork, not yet shipped.
