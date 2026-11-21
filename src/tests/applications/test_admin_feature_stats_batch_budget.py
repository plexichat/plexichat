"""Regression: admin dashboard feature stats use one batched COUNT query.

The cold dashboard build used to issue a separate COUNT (plus a
table-existence probe) for each of ~12 feature counters. Keep the batched
single-query shape so a future refactor cannot silently restore the N+1
pattern on this hot admin path.
"""

import pytest

from src.core.applications.manager.admin import _FEATURE_COUNT_COLUMNS


EXPECTED_KEYS = set(_FEATURE_COUNT_COLUMNS) | {"report_categories"}


class _FakeStatsDB:
    """Minimal DB double that records the batched query and returns fixed data."""

    def __init__(self, row, report_rows):
        self._row = row
        self._report_rows = report_rows
        self.queries = []

    def table_exists(self, table):
        return True

    def fetch_one(self, query, params=None):
        self.queries.append(query)
        return self._row

    def fetch_all(self, query, params=None):
        self.queries.append(query)
        return self._report_rows


@pytest.mark.applications
class TestAdminFeatureStatsBatchBudget:
    def test_batched_query_budget(self, db, app_manager):
        """The whole cold build must stay near one query, not one per counter."""
        calls = []
        original_one = db.fetch_one
        original_all = db.fetch_all

        def counted_one(query, params=None):
            calls.append(query)
            return original_one(query, params)

        def counted_all(query, params=None):
            calls.append(query)
            return original_all(query, params)

        db.fetch_one = counted_one
        db.fetch_all = counted_all
        try:
            stats = app_manager.get_admin_dashboard_feature_stats()
        finally:
            db.fetch_one = original_one
            db.fetch_all = original_all

        print("FEATURE_STATS_QUERY_CALLS=", len(calls))
        for i, query in enumerate(calls, 1):
            print(f"Q{i:02d}", " ".join(query.split()))

        # Old shape: 12 existence probes + 12 COUNTs + 1 GROUP BY = 25.
        # New shape: ~10 existence probes + 1 batched COUNT + 1 GROUP BY = ~12.
        assert len(calls) <= 13, f"feature stats issued {len(calls)} DB calls"
        count_statements = [q for q in calls if "count(" in q.lower()]
        assert len(count_statements) <= 2, (
            "expected at most one batched COUNT plus the grouped report query, "
            f"got {len(count_statements)}"
        )
        assert set(stats) == EXPECTED_KEYS

    def test_batched_columns_map_to_stats(self, app_manager):
        """Conditional-aggregation columns must land in the right keys."""
        row = {column: idx + 1 for idx, column in enumerate(_FEATURE_COUNT_COLUMNS)}
        report_rows = [{"category": "spam", "count": 7}]
        fake = _FakeStatsDB(row, report_rows)

        real_db = app_manager._db
        app_manager._db = fake
        try:
            stats = app_manager.get_admin_dashboard_feature_stats()
        finally:
            app_manager._db = real_db

        for column in _FEATURE_COUNT_COLUMNS:
            assert stats[column] == row[column], column
        assert stats["report_categories"] == report_rows

        batched = fake.queries[0]
        assert batched.count("SUM(CASE WHEN") == 3, batched
        assert "status = 'dead_letter'" in batched
        assert "COALESCE(c_webhook.pending, 0)" in batched
        assert "FROM (SELECT 1) AS seed" in batched
