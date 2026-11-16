"""Regression tests for PlexiSuite launcher projection boundaries.

The suite home feeds synthesize launcher cards from mixed artifact, Stash and
Sum rows. These tests pin the closed app-id catalog, snowflake id validation and
bounded scatter/gather inputs so a malformed row cannot produce a dead card or
an unbounded query.
"""

import pytest

from src.api.routes.suite import registry


class TestSuiteAppIds:
    def test_known_ids_round_trip(self):
        for app_id in ("plexichat", "plexistash", "plexisum", "plexiboard"):
            assert registry.safe_app_id(app_id) == app_id

    @pytest.mark.parametrize("bad", ["", "unknown", "../evil", "<script>"])
    def test_unknown_ids_fall_back(self, bad):
        assert registry.safe_app_id(bad) == "plexichat"

    def test_catalog_mappings_valid(self):
        for mapped in registry._ARTIFACT_APP_IDS.values():
            assert mapped in registry._VALID_APP_IDS


class TestSuiteIds:
    def test_artifact_ids_are_snowflakes(self):
        assert registry.safe_suite_id(123) == "123"
        assert registry.safe_suite_id(None) is None
        for bad in ("-1", "0", "abc", 99999999999999999999999):
            assert registry.safe_suite_id(bad) is None

    def test_timestamps_bounded(self):
        assert registry.safe_timestamp(1234) == 1234
        assert registry.safe_timestamp("bad") == 0
        assert registry.safe_timestamp(-1) == 0
        assert registry.safe_timestamp(10**15) == 0


class TestSuiteSearchBounds:
    def test_search_bounded_and_clean(self):
        assert registry.clean_suite_search("  hello  ") == "hello"
        assert registry.clean_suite_search("x" * 500) == "x" * 200
        assert registry.clean_suite_search(None) is None
        with pytest.raises(ValueError):
            registry.clean_suite_search("a\x00b")
