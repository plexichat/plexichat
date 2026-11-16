"""Suite app ids and artifact ids are checked against declared allowlists."""

import pytest

from src.core.security.hardening import HardeningViolation
from src.core.suite.boundaries import (
    SUITE_APP_IDS,
    SUITE_ARTIFACT_TYPES,
    SUITE_CAPABILITIES,
    validate_app_id,
    validate_artifact_id,
    validate_artifact_type,
)
from src.core.suite.registry.service import (
    APP_IDS,
    SuiteRegistryService,
    validate_manifest,
)


class TestAppIds:
    @pytest.mark.parametrize("app_id", sorted(SUITE_APP_IDS))
    def test_declared_ids_accepted(self, app_id):
        assert validate_app_id(app_id) == app_id

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            None,
            123,
            "no-such-app",
            "../../etc/passwd",
            "__import__",
            "a" * 33,
            "PlexiSum",
        ],
    )
    def test_unknown_ids_rejected(self, bad):
        with pytest.raises(HardeningViolation):
            validate_app_id(bad)


class TestArtifactIds:
    @pytest.mark.parametrize("type_name", sorted(SUITE_ARTIFACT_TYPES))
    def test_declared_types_accepted(self, type_name):
        assert validate_artifact_type(type_name) == type_name

    @pytest.mark.parametrize("bad", ["__import__", "", None, "os.system", 12])
    def test_unknown_types_rejected(self, bad):
        with pytest.raises(HardeningViolation):
            validate_artifact_type(bad)

    def test_artifact_id_is_a_snowflake(self):
        assert validate_artifact_id("123") == 123
        for bad in (0, -1, "abc", 2**63, 1.5):
            with pytest.raises(HardeningViolation):
                validate_artifact_id(bad)


class TestRegistryUsesAllowlist:
    def test_registry_and_boundaries_agree(self):
        assert APP_IDS == SUITE_APP_IDS
        assert all(validate_manifest(m) for m in SuiteRegistryService().list_apps())

    def test_get_app_rejects_unknown(self):
        service = SuiteRegistryService()
        assert service.get_app("plexisum") is not None
        for bad in ("../../etc/passwd", "__import__", "", None):
            assert service.get_app(bad) is None

    def test_get_capability_rejects_unknown(self):
        service = SuiteRegistryService()
        for bad in ("os.system", "not-a-capability", "", None):
            assert service.get_capability(bad).state == "misconfigured"
        assert all(
            c in SUITE_CAPABILITIES
            for c in (
                "plexiscribe",
                "plexiscript",
                "artifacts_whiteboard",
                "plexistash",
                "plexisum",
            )
        )
