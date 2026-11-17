"""Values are bound; only the documented non-bindable fragments are inlined.

``pg_get_serial_sequence()`` used to receive its table name inside a SQL string
literal (``'users'``). That is a *value*, so it is now a bind parameter. The
remaining interpolations — object names and bounded numeric clauses — cannot
be bound and are documented in ``sql_safety.py`` / the builder README.
"""

from typing import cast

import pytest

from src.core.database.core import Database
from src.core.database.builder import QueryBuilder
from src.core.migrations.cloner import DataCloner

pytestmark = [pytest.mark.unit, pytest.mark.security]


class _FakeDB:
    def __init__(self, dbtype, path, rows=None, count=1):
        self.type = dbtype
        self.config = {"type": dbtype, "path": path}
        self._rows = rows or []
        self._count = count
        self.executed = []

    def fetch_all(self, query, params=None):
        if query.startswith("PRAGMA table_info"):
            return [{"name": "id"}, {"name": "content"}]
        if "SELECT * FROM" in query:
            return self._rows
        return []

    def fetch_one(self, query, params=None):
        if "COUNT(*)" in query:
            return {"count": self._count}
        return None

    def begin_transaction(self):
        pass

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def execute_many(self, query, rows):
        self.executed.append((query, rows))

    def rollback(self):
        pass


def test_cloner_binds_serial_sequence_table_name():
    source = _FakeDB("sqlite", "/tmp/src.db", rows=[{"id": 1, "content": "x"}])
    target = _FakeDB("postgres", "dbname=target")
    DataCloner(cast(Database, source), cast(Database, target)).clone_table("users")

    setval = [(q, p) for q, p in target.executed if "pg_get_serial_sequence" in q]
    assert len(setval) == 1
    sql, params = setval[0]
    assert "pg_get_serial_sequence(?, 'id')" in sql
    assert "'users'" not in sql, "table name must not be in a SQL string literal"
    assert tuple(params) == ("users",)
    # The object name in the FROM clause cannot be bound; it stays inlined
    # after passing through require_identifier.
    assert "FROM users" in sql


def test_only_names_and_numeric_clauses_are_inlined():
    builder = QueryBuilder(object(), db_type="sqlite")
    sql, params = (
        builder.table("users")
        .select(["id", "username"])
        .where("id", "=", "1 OR 1=1")
        .limit(10)
        .build()
    )
    assert "SELECT id, username FROM users" in sql
    assert "LIMIT 10" in sql  # bounded numeric literal stays inlined
    assert "id = ?" in sql  # values are placeholders
    assert params[0].value == "1 OR 1=1"
    assert "1 OR 1=1" not in sql
