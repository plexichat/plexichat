"""Contract-only types for the PlexiSuite app registry.

These dataclasses are intentionally not connected to the current capability
routes. They establish the shape that a future registry endpoint can expose to
the client universal home.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class AppManifest:
    """Server-visible metadata for one PlexiSuite application."""

    app_id: str
    label: str
    icon: str
    color_token: str
    artifact_types: List[str] = field(default_factory=list)
    capability: Optional[str] = None
    scopes: List[str] = field(default_factory=list)
    supports_embedded: bool = False
    supports_offline: bool = False
    settings_path: Optional[str] = None
    onboarding_path: Optional[str] = None


@dataclass(frozen=True)
class CapabilityProjection:
    """A safe capability summary suitable for launcher rendering."""

    feature: str
    state: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SuiteRegistryResponse:
    """Future response shape for a capability-aware app catalog."""

    apps: List[AppManifest] = field(default_factory=list)
    capabilities: List[CapabilityProjection] = field(default_factory=list)
    schema_version: int = 1
