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

The implementation branch should add behavior incrementally. This scaffold
contains contracts and decisions only; it does not create migrations or alter
startup behavior.
