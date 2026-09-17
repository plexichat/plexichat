"""Tests for the server-authoritative PlexiSuite registry service.

Covers the security invariants documented in
``src/core/suite/registry/interfaces.py``:

* the server decides which apps are visible/usable (stable launcher order,
  single source of truth shared with the ``/suite/apps`` route);
* unknown app IDs are rejected, never dynamically imported;
* manifest paths are allow-listed and same-origin;
* capability projections never disclose secrets or non-scalar details;
* registry reads are bounded and fail closed (never raise).
"""

import asyncio
import types

from src.core.suite.registry.schema import AppManifest
from src.core.suite.registry.service import (
    APP_IDS,
    SUITE_MANIFESTS,
    SuiteRegistryService,
    get_registry,
    scrub_details,
    validate_manifest,
)


def test_manifests_are_stable_and_in_launcher_order():
    service = SuiteRegistryService()
    apps = service.list_apps()
    assert [m.app_id for m in apps] == [
        "plexichat",
        "plexiscribe",
        "plexiscript",
        "plexiboard",
        "plexistash",
        "plexisum",
    ]
    # Single source of truth: the route serves exactly these manifests.
    from src.api.routes.suite.registry import _SUITE_MANIFESTS

    assert list(_SUITE_MANIFESTS) == list(apps)
    assert set(APP_IDS) == {m.app_id for m in apps}


def test_get_app_rejects_unknown_ids_without_import():
    service = SuiteRegistryService()
    assert service.get_app("plexistash") is not None
    assert service.get_app("no-such-app") is None
    assert service.get_app("") is None
    assert service.get_app(None) is None
    assert service.get_app(123) is None
    # Path-traversal-flavoured IDs are just unknown, never resolved.
    assert service.get_app("../../etc/passwd") is None


def test_validate_manifest_rejects_unknown_app_and_remote_paths():
    good = SUITE_MANIFESTS[0]
    assert validate_manifest(good) is True

    evil_app = AppManifest(
        app_id="evil",
        label="Evil",
        icon="ph-x",
        color_token="evil",
    )
    assert validate_manifest(evil_app) is False

    base = dict(
        app_id="plexistash",
        label="PlexiStash",
        icon="ph-vault",
        color_token="stash",
    )
    for bad_path in (
        "https://evil.example/settings",
        "//evil.example/settings",
        "javascript:alert(1)",
        "relative/path",
        "C:\\windows\\path",
    ):
        assert (
            validate_manifest(AppManifest(**{**base, "settings_path": bad_path}))
            is False
        )
    assert (
        validate_manifest(
            AppManifest(**{**base, "onboarding_path": "/suite/plexistash/onboarding"})
        )
        is True
    )


def test_scrub_details_keeps_safe_scalars_and_drops_the_rest():
    scrubbed = scrub_details(
        {
            "evaluation": True,
            "ttl_days": 30,
            "licensed_feature": "plexisum",
            "db_dsn": "postgres://secret",
            "api_token": "s3cr3t",
            "nested": {"a": 1},
            "items": [1, 2],
            123: "non-string-key",
        }
    )
    assert scrubbed == {
        "evaluation": True,
        "ttl_days": 30,
        "licensed_feature": "plexisum",
    }
    assert scrub_details(None) == {}
    assert scrub_details("not-a-dict") == {}


def test_get_capability_never_raises_and_scrubs():
    service = SuiteRegistryService()
    projection = service.get_capability("plexisum")
    assert projection.feature == "plexisum"
    assert isinstance(projection.state, str) and projection.state
    assert isinstance(projection.details, dict)

    unknown = service.get_capability("no-such-capability")
    assert unknown.state == "misconfigured"

    empty = service.get_capability("")
    assert empty.state == "misconfigured"


def test_project_capability_fails_closed_without_capability_service(monkeypatch):
    import sys

    import src.core.suite.registry.service as service_mod

    monkeypatch.setitem(sys.modules, "src.core.artifacts.capabilities", None)
    # Reload the from-import target failure: the helper must fail closed.
    projection = service_mod.project_capability("plexisum")
    assert projection.feature == "plexisum"
    assert projection.state == "misconfigured"
    assert projection.details == {}


def test_catalog_is_bounded_and_single_sourced():
    service = SuiteRegistryService()
    response = service.catalog()
    assert response.schema_version == 1
    assert [m.app_id for m in response.apps] == [m.app_id for m in SUITE_MANIFESTS]
    # One projection per manifest that declares a capability (plexichat has none).
    assert len(response.capabilities) == sum(1 for m in SUITE_MANIFESTS if m.capability)
    assert {c.feature for c in response.capabilities} == {
        m.capability for m in SUITE_MANIFESTS if m.capability
    }


def test_get_registry_returns_singleton():
    assert get_registry() is get_registry()
    assert isinstance(get_registry(), SuiteRegistryService)


def test_route_apps_match_registry_catalog():
    """The ``/suite/apps`` route serves the registry's manifests verbatim."""
    from src.api.routes.suite import registry as registry_routes

    result = asyncio.run(
        registry_routes.list_suite_apps(current_user=types.SimpleNamespace(user_id=42))
    )
    assert result["schemaVersion"] == 1
    assert [app["id"] for app in result["apps"]] == [
        m.app_id for m in get_registry().list_apps()
    ]


def test_route_capability_state_is_scrubbed():
    """The route must not leak raw capability details (e.g. provider names)."""
    from src.api.routes.suite import registry as registry_routes
    from src.core.artifacts.capabilities import CapabilityInfo, CapabilityState

    raw = CapabilityInfo(
        feature="plexisum",
        state=CapabilityState.AVAILABLE,
        message="ok",
        details={
            "feature_enabled": True,
            "provider": "openai",
            "api_token": "s3cr3t",
            "nested": {"a": 1},
        },
    )
    manifest = get_registry().get_app("plexisum")
    assert manifest is not None
    serialized = registry_routes._manifest_dict(manifest, {"plexisum": raw})
    assert serialized["capabilityState"]["details"] == {"feature_enabled": True}
    assert serialized["availability"] == "available"

    result = asyncio.run(
        registry_routes.list_suite_apps(current_user=types.SimpleNamespace(user_id=42))
    )
    for app in result["apps"]:
        details = (app.get("capabilityState") or {}).get("details", {})
        assert "provider" not in details
        assert "api_token" not in details
        for value in details.values():
            assert value is None or isinstance(value, (bool, int, str))


def test_manifest_to_dict_matches_route_serializer():
    """The service serializer is the single source of truth for the route."""
    from src.api.routes.suite import registry as registry_routes
    from src.core.suite.registry.service import manifest_to_dict

    registry = get_registry()
    for manifest in registry.list_apps():
        projection = (
            registry.get_capability(manifest.capability)
            if manifest.capability
            else None
        )
        assert registry_routes._manifest_dict(
            manifest,
            {manifest.capability: projection} if manifest.capability else {},
        ) == manifest_to_dict(manifest, projection)
