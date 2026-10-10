"""Interfaces for the PlexiSuite registry service.

The interface is deliberately small: the registry describes apps; it does not
make authorization decisions or replace the existing capability service.
"""

from typing import Iterable, Optional, Protocol

from .schema import AppManifest, CapabilityProjection, SuiteRegistryResponse


class SuiteRegistry(Protocol):
    """Read-only registry contract served by ``GET /suite/apps``."""

    def list_apps(self) -> Iterable[AppManifest]:
        """Return stable manifests in launcher order."""
        ...

    def get_app(self, app_id: str) -> Optional[AppManifest]:
        """Return the manifest for a known app ID, else ``None``."""
        ...

    def get_capability(self, feature: str) -> CapabilityProjection:
        """Return the authoritative scrubbed projection for one feature."""
        ...

    def catalog(self) -> SuiteRegistryResponse:
        """Return the bounded capability-aware catalog for launcher rendering."""
        ...

    def iter_manifests(self) -> Iterable[AppManifest]:
        """Iterate manifests in launcher order (convenience alias)."""
        ...


# Security invariants for the eventual implementation:
#
# * The server decides which apps are visible/usable; the client only renders.
# * Unknown app IDs and artifact types are rejected, never dynamically imported.
# * Manifest paths are allow-listed and same-origin.
# * Capability details must not disclose secrets or inaccessible resource names.
# * Registry responses are bounded and cacheable per user/server capability scope.
