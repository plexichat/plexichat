"""Every SQL identifier in the owned builders flows through require_identifier.

The builder, the migration cloner and the SQLite FTS indexer used to each keep
their own idea of "valid name" (regexes, manual backtick quoting, or nothing).
This module pins the single shared rule from
``src.core.security.hardening.require_identifier`` as the only path an object
name can take into a statement that cannot be parameterized.
"""

import pytest

from src.core.database.builder import QueryBuilder, SQLInjectionError
from src.core.database.builder import sql_safety
from src.core.migrations.cloner import DataCloner
from src.core.search.indexer import sqlite_fts
from src.core.security.hardening import HardeningViolation

pytestmark = [pytest.mark.unit, pytest.mark.security]


class _Spy:
    """Callable that records the values it is asked to validate."""

    def __init__(self, real):
        self.real = real
        self.calls = []

    def __call__(self, value, field="identifier"):
        self.calls.append((value, field))
        return self.real(value, field)


def test_sql_identifier_delegates_to_require_identifier(monkeypatch):
    spy = _Spy(sql_safety.require_identifier)
    monkeypatch.setattr(sql_safety, "require_identifier", spy)

    assert sql_safety.sql_identifier("public.users", "table") == "public.users"
    assert [value for value, _ in spy.calls] == ["public", "users"]
    assert all(field == "table" for _, field in spy.calls)


@pytest.mark.parametrize(
    "name",
    [
        "users; DROP TABLE users; --",
        "users'--",
        "users/*x*/",
        "üsers",
        "users\x00",
    ],
)
def test_builder_rejects_injected_identifiers(name):
    builder = QueryBuilder(object(), db_type="sqlite")
    with pytest.raises(SQLInjectionError):
        builder.table(name).select().build()
    with pytest.raises(SQLInjectionError):
        builder.table("users").select([name]).build()


def test_builder_rejects_overlong_identifier_via_shared_rule():
    """The 63-char bound comes from require_identifier, not a local regex."""
    builder = QueryBuilder(object(), db_type="sqlite")
    with pytest.raises(SQLInjectionError):
        builder.table("a" * 64).select().build()
    assert builder.table("a" * 63).select().build()[0].startswith("SELECT")


def test_builder_accepts_dotted_name_but_validates_each_segment():
    builder = QueryBuilder(object(), db_type="sqlite")
    sql, _ = builder.table("users").select().where("users.id", "=", 1).build()
    assert "users.id = ?" in sql
    with pytest.raises(SQLInjectionError):
        builder.table("users").select().where("users.id; --", "=", 1).build()


class _FakeDB:
    def __init__(self, path, dbtype="sqlite"):
        self.type = dbtype
        self.config = {"type": dbtype, "path": path}

    def fetch_all(self, *args, **kwargs):
        raise AssertionError("no statement should run for a rejected name")

    def fetch_one(self, *args, **kwargs):
        raise AssertionError("no statement should run for a rejected name")


@pytest.mark.parametrize(
    "name", ["users; DROP TABLE users", "users' --", "users/*x*/", "üsers"]
)
def test_cloner_validates_table_name_before_any_statement(name):
    cloner = DataCloner(_FakeDB("/tmp/a.db"), _FakeDB("/tmp/b.db"))
    with pytest.raises(HardeningViolation):
        cloner.clone_table(name)


def test_cloner_identifier_paths_share_the_rule(monkeypatch):
    spy = _Spy(sql_safety.require_identifier)
    monkeypatch.setattr(sql_safety, "require_identifier", spy)
    cloner = DataCloner(_FakeDB("/tmp/a.db"), _FakeDB("/tmp/b.db"))

    with pytest.raises(HardeningViolation):
        cloner.clone_table("users; --")
    assert spy.calls, "cloner must reach the shared identifier validator"


def test_sqlite_fts_probe_routes_table_names_through_shared_rule(monkeypatch):
    spy = _Spy(sql_safety.require_identifier)
    monkeypatch.setattr(sqlite_fts, "sql_identifier", spy)

    indexer = sqlite_fts.SQLiteFTS5Indexer(_FakeDB("/tmp/a.db"))
    # The hardcoded table names are valid; inject a hostile one to prove the
    # probe funnels it through require_identifier rather than quoting it itself.
    monkeypatch.setattr(
        sqlite_fts,
        "_FTS_EXPECTED_COLUMNS",
        {"search_'; DROP TABLE x; --": ["id"]},
    )
    with pytest.raises(HardeningViolation):
        indexer._probe_fts_schema()
    assert spy.calls
