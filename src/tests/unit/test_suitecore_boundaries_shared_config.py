"""Suite shared config is restricted to a declared key/value schema."""

import pytest

from src.core.suite.boundaries import (
    SHARED_CONFIG_SCHEMA,
    validate_shared_config,
)
from src.core.suite.stash.cleanup import StashCleanupJob, _stash_config


class TestSharedConfigSchema:
    def test_defaults_for_empty_config(self):
        assert validate_shared_config({}) == {
            key: rule.default for key, rule in SHARED_CONFIG_SCHEMA.items()
        }

    def test_declared_values_honoured(self):
        result = validate_shared_config(
            {
                "cleanup_interval_minutes": 15,
                "retention_days": 90,
                "workspace_theme": "dark",
            }
        )
        assert result["cleanup_interval_minutes"] == 15
        assert result["retention_days"] == 90
        assert result["workspace_theme"] == "dark"

    def test_unknown_keys_dropped(self):
        result = validate_shared_config(
            {"drop_all": True, "secret": "hunter2", "cleanup_interval_minutes": 5}
        )
        assert "drop_all" not in result
        assert "secret" not in result
        assert result["cleanup_interval_minutes"] == 5

    @pytest.mark.parametrize(
        "key,bad",
        [
            ("cleanup_interval_minutes", 0),
            ("cleanup_interval_minutes", 10**9),
            ("retention_days", -1),
            ("retention_days", "later"),
            ("max_items_per_page", 10**6),
            ("workspace_theme", "evil;rm -rf /"),
        ],
    )
    def test_bad_values_fall_back_to_default(self, key, bad):
        result = validate_shared_config({key: bad})
        assert result[key] == SHARED_CONFIG_SCHEMA[key].default

    @pytest.mark.parametrize("bad", [None, "nonsense", 123])
    def test_non_mapping_config(self, bad):
        assert validate_shared_config(bad) == {
            key: rule.default for key, rule in SHARED_CONFIG_SCHEMA.items()
        }

    def test_section_lookup(self):
        result = validate_shared_config(
            {"artifacts": {"plexistash": {"retention_days": 7}}},
            section="artifacts",
        )
        # The section value must be a mapping of declared keys; wrapping one
        # more level yields defaults rather than reading a nested section.
        assert (
            result["retention_days"] == SHARED_CONFIG_SCHEMA["retention_days"].default
        )


class TestStashCleanupUsesSchema:
    def test_stash_config_is_schema_bounded(self):
        raw = {
            "plexistash": {
                "cleanup_interval_minutes": 10**9,
                "retention_days": -5,
                "secret": "x",
            }
        }
        bounded = _stash_config(raw)
        assert "secret" not in bounded
        assert bounded["cleanup_interval_minutes"] == 60
        assert bounded["retention_days"] == 30

    def test_interval_and_retention_are_bounded(self):
        job = StashCleanupJob(
            db=None,
            config={
                "plexistash": {"cleanup_interval_minutes": 10**9, "retention_days": -1}
            },
        )
        assert job._interval_seconds() == 60 * 60
        assert job._retention_days() == 30
