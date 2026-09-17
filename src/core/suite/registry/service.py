"""Server-authoritative PlexiSuite app registry service.

This module is the single source of truth for the app catalog exposed at
``GET /suite/apps``. The manifests live here (not in the route layer) so the
server decides which apps are visible/usable and the client only renders.

Security invariants (enforced here, not by callers):

* Unknown app IDs are rejected (``get_app`` returns ``None``); nothing is
  ever dynamically imported from an app ID or artifact type.
* Manifest ``settings_path`` / ``onboarding_path`` values must be
  allow-listed same-origin paths (``None`` or ``"/..."`` without a scheme,
  host, or protocol-relative ``//`` prefix).
* Capability projections are scrubbed: only JSON-scalar details under
  known-safe keys survive, so launcher payloads can never disclose secrets
  or inaccessible resource names.
* Responses are bounded (six manifests, one projection per capability) and
  safe to cache per user/server capability scope.
"""

from typing import Any, Dict, Iterable, Optional, Tuple

from .interfaces import SuiteRegistry
from .schema import AppManifest, CapabilityProjection, SuiteRegistryResponse

# Canonical launcher order. The route layer must import this instead of
# maintaining its own copy.
SUITE_MANIFESTS: Tuple[AppManifest, ...] = (
    AppManifest(
        app_id="plexichat",
        label="Plexichat",
        icon="ph-chat-circle",
        color_token="chat",
        scopes=["personal", "conversation", "server"],
        supports_embedded=False,
        supports_offline=True,
    ),
    AppManifest(
        app_id="plexiscribe",
        label="PlexiScribe",
        icon="ph-notebook",
        color_token="scribe",
        artifact_types=["plexiscribe"],
        capability="plexiscribe",
        scopes=["personal", "conversation", "server"],
        supports_embedded=True,
        supports_offline=True,
    ),
    AppManifest(
        app_id="plexiscript",
        label="PlexiScript",
        icon="ph-code",
        color_token="script",
        artifact_types=["plexiscript"],
        capability="plexiscript",
        scopes=["personal", "conversation", "server"],
        supports_embedded=True,
        supports_offline=True,
    ),
    AppManifest(
        app_id="plexiboard",
        label="PlexiBoard",
        icon="ph-chalkboard",
        color_token="board",
        artifact_types=["whiteboard"],
        capability="artifacts_whiteboard",
        scopes=["conversation", "server"],
        supports_embedded=True,
        supports_offline=False,
    ),
    AppManifest(
        app_id="plexistash",
        label="PlexiStash",
        icon="ph-vault",
        color_token="stash",
        artifact_types=["stash"],
        capability="plexistash",
        scopes=["personal", "server"],
        supports_embedded=True,
        supports_offline=True,
    ),
    AppManifest(
        app_id="plexisum",
        label="PlexiSum",
        icon="ph-table",
        color_token="sum",
        artifact_types=["sum"],
        capability="plexisum",
        scopes=["personal", "server"],
        supports_embedded=False,
        supports_offline=True,
    ),
)

APP_IDS = frozenset(manifest.app_id for manifest in SUITE_MANIFESTS)

_BY_APP_ID: Dict[str, AppManifest] = {m.app_id: m for m in SUITE_MANIFESTS}

# Detail keys the capability evaluators are known to emit (see
# ``src/core/artifacts/capabilities.py``). Anything else is dropped so a
# future evaluator cannot leak a path, token, or resource name into the
# launcher payload.
_SAFE_DETAIL_KEYS = frozenset(
    {
        "evaluation",
        "ttl_days",
        "licensed_feature",
        "enabled",
        "artifacts_enabled",
        "plexiscribe_enabled",
        "plexiscript_enabled",
        "whiteboard_enabled",
        "feature_enabled",
        "allow_recording",
    }
)

_SUSPICIOUS_KEY_HINTS = ("secret", "token", "password", "key", "path", "dsn", "url")


def _is_safe_path(value: Optional[str]) -> bool:
    """Return whether an optional manifest path is allow-listed same-origin."""
    if value is None:
        return True
    if not isinstance(value, str) or not value.startswith("/"):
        return False
    if value.startswith("//") or "://" in value or "\\" in value:
        return False
    return True


def validate_manifest(manifest: AppManifest) -> bool:
    """Return whether a manifest satisfies the registry's safety invariants."""
    if manifest.app_id not in APP_IDS:
        return False
    return _is_safe_path(manifest.settings_path) and _is_safe_path(
        manifest.onboarding_path
    )


def scrub_details(details: Any) -> Dict[str, Any]:
    """Scrub a capability ``details`` mapping down to launcher-safe scalars."""
    if not isinstance(details, dict):
        return {}
    scrubbed: Dict[str, Any] = {}
    for key, value in details.items():
        if not isinstance(key, str):
            continue
        lowered = key.lower()
        if key not in _SAFE_DETAIL_KEYS:
            continue
        if any(hint in lowered for hint in _SUSPICIOUS_KEY_HINTS):
            continue
        if value is None or isinstance(value, (bool, int, str)):
            scrubbed[key] = value
    return scrubbed


def project_capability(
    feature: str, config: Optional[Dict[str, Any]] = None
) -> CapabilityProjection:
    """Return the launcher-safe capability projection for one feature.

    Unknown features resolve to ``misconfigured`` (via the underlying
    capability service) and this helper never raises: a registry read must
    not fail because one capability evaluator did.
    """
    try:
        from src.core.artifacts.capabilities import get_capability
    except Exception:
        return CapabilityProjection(
            feature=str(feature),
            state="misconfigured",
            message="Capability service unavailable.",
            details={},
        )
    try:
        info = get_capability(feature, config)
        state = getattr(info.state, "value", str(info.state))
        message = str(getattr(info, "message", ""))
        return CapabilityProjection(
            feature=str(feature),
            state=state,
            message=message,
            details=scrub_details(getattr(info, "details", {})),
        )
    except Exception:
        return CapabilityProjection(
            feature=str(feature),
            state="misconfigured",
            message="Unable to evaluate the requested capability.",
            details={},
        )


def manifest_to_dict(
    manifest: AppManifest, projection: Optional[CapabilityProjection] = None
) -> Dict[str, Any]:
    """Serialize a manifest to the launcher contract with scrubbed capability.

    This is the single serializer for ``GET /suite/apps`` so the route cannot
    drift back to leaking raw ``CapabilityInfo.details``. ``projection`` must
    already be scrubbed (e.g. from :meth:`SuiteRegistryService.get_capability`
    or :func:`project_capability`); ``None`` means the app declares no
    capability (e.g. ``plexichat``).
    """
    result: Dict[str, Any] = {
        "id": manifest.app_id,
        "label": manifest.label,
        "icon": manifest.icon,
        "color": manifest.color_token,
        "artifactTypes": list(manifest.artifact_types),
        "capability": manifest.capability,
        "scopes": list(manifest.scopes),
        "supportsEmbedded": manifest.supports_embedded,
        "supportsOffline": manifest.supports_offline,
        "availability": "available",
    }
    if projection is not None:
        result["capabilityState"] = {
            "feature": projection.feature,
            "state": projection.state,
            "message": projection.message,
            "details": dict(projection.details),
        }
        result["availability"] = projection.state
    return result


class SuiteRegistryService(SuiteRegistry):
    """Concrete read-only registry; the server decides, the client renders."""

    def list_apps(self) -> Tuple[AppManifest, ...]:
        """Return stable manifests in launcher order."""
        return SUITE_MANIFESTS

    def get_app(self, app_id: str) -> Optional[AppManifest]:
        """Return the manifest for a known app ID, else ``None``.

        Unknown IDs are rejected, never dynamically imported.
        """
        if not isinstance(app_id, str):
            return None
        return _BY_APP_ID.get(app_id)

    def get_capability(self, feature: str) -> CapabilityProjection:
        """Return the authoritative scrubbed projection for one feature."""
        if not isinstance(feature, str) or not feature:
            return CapabilityProjection(
                feature=str(feature),
                state="misconfigured",
                message="Unknown capability feature.",
                details={},
            )
        return project_capability(feature)

    def catalog(self) -> SuiteRegistryResponse:
        """Return the bounded capability-aware catalog for launcher rendering."""
        capabilities = [
            self.get_capability(manifest.capability)
            for manifest in SUITE_MANIFESTS
            if manifest.capability
        ]
        return SuiteRegistryResponse(
            apps=list(SUITE_MANIFESTS),
            capabilities=capabilities,
            schema_version=1,
        )

    def iter_manifests(self) -> Iterable[AppManifest]:
        """Iterate manifests in launcher order (convenience alias)."""
        return iter(SUITE_MANIFESTS)


_registry: Optional[SuiteRegistryService] = None


def get_registry() -> SuiteRegistryService:
    """Return the process-local registry singleton."""
    global _registry
    if _registry is None:
        _registry = SuiteRegistryService()
    return _registry


__all__ = [
    "APP_IDS",
    "SUITE_MANIFESTS",
    "SuiteRegistryService",
    "get_registry",
    "manifest_to_dict",
    "project_capability",
    "scrub_details",
    "validate_manifest",
]
