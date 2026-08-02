"""PlexiSuite backend contract layer.

This package is a design and contract boundary for the PlexiSuite expansion. It
is intentionally not registered by application startup and does not change the
existing artifact schema, routes, or WebSocket gateway.

Plexichat remains the core platform. Suite apps use its identity, permissions,
media/storage, artifact identity, notifications, and realtime infrastructure.
The suite package describes app-specific domain contracts that should be
implemented only after review and tests exist.

Packages:

* ``registry`` — app manifests and universal-home capability projections.
* ``stash`` — Drive-like file/collection metadata and security contracts.
* ``sum`` — workbook metadata, viewport queries, operation envelopes, and
  persistence/synchronization boundaries.

Do not import these contracts into the current boot path until the corresponding
API and migration plan has been approved.
"""
