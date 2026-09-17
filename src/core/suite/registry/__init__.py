"""PlexiSuite app registry contracts and server-authoritative service."""

from .interfaces import SuiteRegistry
from .schema import AppManifest, CapabilityProjection, SuiteRegistryResponse
from .service import (
    APP_IDS,
    SUITE_MANIFESTS,
    SuiteRegistryService,
    get_registry,
    manifest_to_dict,
    project_capability,
    scrub_details,
    validate_manifest,
)

__all__ = [
    "APP_IDS",
    "SUITE_MANIFESTS",
    "AppManifest",
    "CapabilityProjection",
    "SuiteRegistry",
    "SuiteRegistryResponse",
    "SuiteRegistryService",
    "get_registry",
    "manifest_to_dict",
    "project_capability",
    "scrub_details",
    "validate_manifest",
]
