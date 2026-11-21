"""Regression: data-mutating statements hidden behind a leading CTE or SQL
comment must still invalidate the database query-result cache.

``Database.execute()`` used to decide whether to clear ``_query_cache`` by
checking whether the raw SQL text *started* with INSERT/UPDATE/DELETE/... .
Two real write forms slipped past that prefix check while still mutating the
table:

* a common-table expression, e.g. ``WITH d AS (SELECT 1) UPDATE authz ...``
  (SQLite and PostgreSQL both execute this as a write), and
* a write preceded by a SQL comment, e.g. ``-- note\\nUPDATE authz ...`` or
  ``# note\\nUPDATE authz ...`` (the preparation pipeline strips the comment
  before handing the statement to the driver).

In both cases a cached SELECT over the mutated table kept serving the
pre-write snapshot for the cache's whole TTL, so an authorization verdict
such as a role check could survive a demotion or ban. The invalidation check
now fails closed: anything that is not a known read-only statement clears the
short-lived query cache.
"""

import pytest
import utils.config as config
from src.core.database.core import Database

pytestmark = [pytest.mark.unit, pytest.mark.security]


@pytest.fixture
def db(tmp_path):
    Database.reset()
    config.setup(
        config_path=str(tmp_path / "config.yaml"),
        default_config={
            "database": {
                "type": "sqlite",
                "path": str(tmp_path / "coherence.db"),
            }
        },
    )
    Database.reset()
    instance = Database()
    instance.connect()
    instance.execute(
        "CREATE TABLE authz_roles (user_id INTEGER PRIMARY KEY, role TEXT NOT NULL)"
    )
    instance.execute("INSERT INTO authz_roles (user_id, role) VALUES (1, 'admin')")
    # Drop whatever the setup writes put in the query cache so every case below
    # starts from a clean, warm-on-demand cache.
    with instance._query_cache_lock:
        instance._query_cache.clear()
    yield instance
    instance.close()
    Database.reset()


def _role(db):
    row = db.fetch_one("SELECT role FROM authz_roles WHERE user_id = ?", (1,))
    assert row is not None
    return row["role"]


def test_cte_write_invalidates_cached_select(db):
    """A cached 'admin' verdict must not survive a demotion issued as a CTE."""
    assert _role(db) == "admin"  # warms the query-result cache

    # First prove the statement really is a write...
    cursor = db.execute(
        "WITH demote AS (SELECT 1 AS marker) "
        "UPDATE authz_roles SET role = 'user' WHERE user_id = 1"
    )
    cursor.close()

    # ...then prove the cached SELECT no longer serves the stale 'admin' row.
    assert _role(db) == "user"


def test_leading_line_comment_write_invalidates_cached_select(db):
    assert _role(db) == "admin"

    cursor = db.execute(
        "-- administrative demotion\n"
        "UPDATE authz_roles SET role = 'user' WHERE user_id = 1"
    )
    cursor.close()

    assert _role(db) == "user"


def test_leading_hash_comment_write_invalidates_cached_select(db):
    assert _role(db) == "admin"

    cursor = db.execute(
        "# administrative demotion\n"
        "UPDATE authz_roles SET role = 'user' WHERE user_id = 1"
    )
    cursor.close()

    assert _role(db) == "user"


def test_cte_batch_write_invalidates_cached_select(db):
    """The same fail-closed check must guard execute_many() batches."""
    db.execute("INSERT INTO authz_roles (user_id, role) VALUES (2, 'admin')")
    assert _role(db) == "admin"
    assert (
        db.fetch_one("SELECT role FROM authz_roles WHERE user_id = ?", (2,))["role"]
        == "admin"
    )

    cursor = db.execute_many(
        "WITH demote AS (SELECT 1 AS marker) "
        "UPDATE authz_roles SET role = 'user' WHERE user_id = ?",
        [(1,), (2,)],
    )
    cursor.close()

    assert _role(db) == "user"
    assert (
        db.fetch_one("SELECT role FROM authz_roles WHERE user_id = ?", (2,))["role"]
        == "user"
    )
