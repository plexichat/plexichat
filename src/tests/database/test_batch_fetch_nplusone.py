"""Focused regression test for batched key lookups in the database layer.

The hot repository paths resolve parent/child rows by id. Doing that with one
``fetch_one`` per id is the classic N+1 pattern; ``fetch_many_by_ids`` collapses
the loop into a bounded number of ``IN (...)`` statements. These tests measure
the query count directly through the request metrics so a future refactor that
silently reintroduces the loop fails here.
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
                "path": str(tmp_path / "batch.db"),
            }
        },
    )
    Database.reset()
    db = Database()
    db.connect()
    db.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, label TEXT)")
    for item_id in range(1, 41):
        db.execute(
            "INSERT INTO items (id, label) VALUES (?, ?)",
            (item_id, f"item-{item_id}"),
        )
    with db._query_cache_lock:
        db._query_cache.clear()
    yield db
    db.close()
    Database.reset()


def _clear_cache(db):
    with db._query_cache_lock:
        db._query_cache.clear()


class TestBatchedKeyLookup:
    def test_batch_replaces_n_plus_one(self, db_env):
        db = db_env
        ids = list(range(1, 41))

        # BEFORE: one SELECT per id — 40 statements for 40 rows.
        _clear_cache(db)
        db.reset_request_metrics()
        naive = {}
        for item_id in ids:
            row = db.fetch_one("SELECT * FROM items WHERE id = ?", (item_id,))
            if row:
                naive[item_id] = row
        naive_queries = db.get_request_metrics()["query_count"]

        # AFTER: one bounded IN(...) statement for the same 40 rows.
        _clear_cache(db)
        db.reset_request_metrics()
        batched = db.fetch_many_by_ids("items", ids)
        batch_queries = db.get_request_metrics()["query_count"]

        assert naive_queries == len(ids)
        assert batch_queries == 1
        assert batched == naive

    def test_chunking_bounds_query_count(self, db_env):
        db = db_env
        _clear_cache(db)
        db.reset_request_metrics()
        rows = db.fetch_many_by_ids("items", list(range(1, 1101)), batch_size=500)
        # 1100 ids -> 3 chunks; only 40 rows exist.
        assert db.get_request_metrics()["query_count"] == 3
        assert len(rows) == 40
        assert rows[40]["label"] == "item-40"

    def test_empty_deduplicated_and_missing_ids(self, db_env):
        db = db_env
        _clear_cache(db)
        db.reset_request_metrics()
        assert db.fetch_many_by_ids("items", []) == {}
        assert db.get_request_metrics()["query_count"] == 0

        # Duplicate and absent ids neither duplicate work nor create rows.
        rows = db.fetch_many_by_ids("items", [1, 1, 2, 9999])
        assert set(rows) == {1, 2}
        assert 9999 not in rows

    def test_projection_and_identifier_validation(self, db_env):
        db = db_env
        rows = db.fetch_many_by_ids("items", [1], columns=["label"])
        assert set(rows[1]) == {"id", "label"}

        with pytest.raises(HardeningViolation):
            db.fetch_many_by_ids("items; DROP TABLE items", [1])
