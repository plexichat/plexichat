"""Interfaces for a future PlexiSuite registry service.

The interface is deliberately small: the registry describes apps; it does not
make authorization decisions or replace the existing capability service.
"""

from typing import Iterable, Protocol

from .schema import AppManifest, CapabilityProjection


class SuiteRegistry(Protocol):
    """Read-only registry contract used by a future API route."""

    def list_apps(self) -> Iterable[AppManifest]:
        """Return stable manifests in launcher order."""
        ...

    def get_capability(self, feature: str) -> CapabilityProjection:
        """Return the authoritative capability projection for one feature."""
        ...


# Security invariants for the eventual implementation:
#
# * The server decides which apps are visible/usable; the client only renders.
# * Unknown app IDs and artifact types are rejected, never dynamically imported.
# * Manifest paths are allow-listed and same-origin.
# * Capability details must not disclose secrets or inaccessible resource names.
# * Registry responses are bounded and cacheable per user/server capability scope.
