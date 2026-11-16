"""Focused regression tests for the database/migration/fts hardening wave.

These guard the boundaries added at the SQL/migration/indexer layer: resource
sizes that must be bounded, identifiers that must be constrained before they
are interpolated, and queries whose complexity must be capped.
"""

import pytest

from src.core.security.hardening import HardeningViolation


class TestPoolSizeBounds:
    def test_rejects_unbounded_min_size(self):
        from src.core.database.engines.pool import ConnectionPool

        created = []
        with pytest.raises(HardeningViolation):
            ConnectionPool(lambda: created.append(1), 10**9, 10**9 + 1)
        assert created == []

    def test_rejects_min_greater_than_max(self):
        from src.core.database.engines.pool import ConnectionPool

        with pytest.raises(ValueError, match="min_size cannot exceed max_size"):
            ConnectionPool(lambda: object(), 5, 2)


class TestIdentifierBound:
    def test_rejects_overlong_identifier(self):
        from src.core.database.dialect import sanitize_identifier

        with pytest.raises(HardeningViolation):
            sanitize_identifier("a" * 64, "sqlite")

    def test_accepts_normal_identifier(self):
        from src.core.database.dialect import sanitize_identifier

        assert sanitize_identifier("msg_messages", "sqlite") == "`msg_messages`"
        assert sanitize_identifier("msg_messages", "postgres") == '"msg_messages"'
