"""Focused regression test for the bounded prepared-statement cache.

Before: ``Database.execute()`` re-ran comment stripping, placeholder
conversion and (for PostgreSQL) type rewrites on every call, so the hottest
queries paid the same pure-CPU cost again and again. After: the converted SQL
is prepared once and reused from a bounded, instrumented cache. These tests
measure the conversion call count directly and check that caching statement
text never caches rows.
"""

import pytest

import utils.config as config
from src.core.database.core import Database
from src.core.security.hardening import HardeningViolation


@pytest.fixture
def db_env(tmp_path):
    Database.reset()
    config.setup(
        config_path=str(tmp_path / "config.yaml"),
        default_config={
            "database": {
                "type": "sqlite",
                "path": str(tmp_path / "statements.db"),
            }
        },
    )
    Database.reset()
    db = Database()
    db.connect()
    db.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, label TEXT)")  # pyright: ignore[reportAttributeAccessIssue]
    with db._query_cache_lock:
        db._query_cache.clear()
    yield db
    db.close()
    Database.reset()


class TestPreparedStatementCache:
    def test_reuses_and_bounds(self):
        from src.core.database.core.statements import PreparedStatementCache

        cache = PreparedStatementCache(maxsize=2)
        calls = []

        def factory(key):
            calls.append(key)
            return f"sql-{key}"

        first = cache.get_or_prepare("a", lambda: factory("a"))
        second = cache.get_or_prepare("a", lambda: factory("a"))
        assert first is second
        assert calls == ["a"]

        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1

        cache.get_or_prepare("b", lambda: factory("b"))
        cache.get_or_prepare("c", lambda: factory("c"))
        assert len(cache) == 2
        assert cache.stats()["evictions"] == 1

        cache.clear()
        assert len(cache) == 0

    def test_rejects_unbounded_size(self):
        from src.core.database.core.statements import PreparedStatementCache

        with pytest.raises(HardeningViolation):
            PreparedStatementCache(maxsize=10**9)


class TestStatementReuseInExecution:
    def test_repeated_query_is_prepared_once(self, db_env, monkeypatch):
        import src.core.database.dialect as dialect

        original = dialect.convert_placeholders
        calls = []

        def counting(query, db_type):
            calls.append(query)
            return original(query, db_type)

        monkeypatch.setattr(dialect, "convert_placeholders", counting)

        db = db_env
        db._statement_cache.reset_stats()
        db.reset_request_metrics()

        # Distinct parameters defeat the row-result cache, so all 25 calls
        # really execute -- only statement preparation should be shared.
        for item_id in range(1, 26):
            assert (
                db.fetch_one("SELECT label FROM items WHERE id = ?", (item_id,)) is None
            )

        assert db.get_request_metrics()["query_count"] == 25
        assert len(calls) == 1
        stats = db.get_statement_cache_stats()
        assert stats["misses"] == 1
        assert stats["hits"] == 24

    def test_execute_many_shares_one_preparation(self, db_env, monkeypatch):
        import src.core.database.dialect as dialect

        original = dialect.convert_placeholders
        calls = []

        def counting(query, db_type):
            calls.append(query)
            return original(query, db_type)

        monkeypatch.setattr(dialect, "convert_placeholders", counting)

        db_env.execute_many(
            "INSERT INTO items (label) VALUES (?)", [("a",), ("b",), ("c",)]
        )
        assert len(calls) == 1

    def test_cached_statement_does_not_cache_rows(self, db_env):
        db = db_env
        query = "SELECT COUNT(*) AS n FROM items"
        assert db.fetch_one(query)["n"] == 0

        db.execute("INSERT INTO items (label) VALUES (?)", ("fresh",))
        # The write invalidates the row cache; the statement cache must keep
        # only SQL text so the next read observes the new row.
        assert db.fetch_one(query)["n"] == 1
