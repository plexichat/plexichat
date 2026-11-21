"""Regression tests for shape-keyed caching of assembled builder SQL text.

The builders used to re-assemble identical SQL (f-string WHERE fragments,
dict-key-ordered SET/VALUES lists, LIMIT/OFFSET suffixes) on every call.  The
statement-text cache must make repeated identical shapes reuse one compiled
string while values stay bound parameters, without weakening any validation.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest

from src.core.database.builder import QueryBuilder, SchemaValidationError
from src.core.database.builder.base import SQLInjectionError
from src.core.database.builder.delete import DeleteQuery
from src.core.database.builder.insert import InsertQuery
from src.core.database.builder.select import SelectQuery
from src.core.database.builder.statement_cache import (
    reset_statement_text_cache,
    statement_text_cache_stats,
)
from src.core.database.builder.update import UpdateQuery

pytestmark = [pytest.mark.unit, pytest.mark.security]


@pytest.fixture(autouse=True)
def _clean_cache():
    """Isolate cache statistics between tests."""
    reset_statement_text_cache()
    yield
    reset_statement_text_cache()


def _values(params):
    return [getattr(p, "value", p) for p in params]


# --- reuse: one SQL text per shape, values always rebound -------------------


def test_identical_shape_reuses_compiled_sql_text():
    first_sql, first_params = (
        SelectQuery(None, "users", ["id", "name"])
        .where("id", "=", 1)
        .where("name", "=", "alice")
        .limit(10)
        .build()
    )
    second_sql, second_params = (
        SelectQuery(None, "users", ["id", "name"])
        .where("id", "=", 999)
        .where("name", "=", "bob")
        .limit(10)
        .build()
    )

    assert first_sql == second_sql
    assert first_sql == (
        "SELECT id, name FROM users WHERE id = ? AND name = ? LIMIT 10"
    )
    # Same shape -> the second build is a cache hit.
    assert statement_text_cache_stats()["hits"] == 1
    assert statement_text_cache_stats()["misses"] == 1

    # Values are rebound, never reused from the cached string.
    assert _values(first_params) == [1, "alice"]
    assert _values(second_params) == [999, "bob"]
    assert "alice" not in second_sql and "999" not in second_sql


def test_all_statement_kinds_share_text_across_values():
    cases = [
        (
            lambda v: SelectQuery(None, "users").where("id", "=", v).build(),
            "SELECT * FROM users WHERE id = ?",
        ),
        (
            lambda v: InsertQuery(None, "users", {"name": v}).build(),
            "INSERT INTO users (name) VALUES (?)",
        ),
        (
            lambda v: (
                UpdateQuery(None, "users", {"name": v}).where("id", "=", 7).build()
            ),
            "UPDATE users SET name = ? WHERE id = ?",
        ),
        (
            lambda v: DeleteQuery(None, "users").where("id", "=", v).build(),
            "DELETE FROM users WHERE id = ?",
        ),
    ]
    for build, expected in cases:
        reset_statement_text_cache()
        sql_a, _ = build("a")
        sql_b, _ = build("b")
        assert sql_a == sql_b == expected
        assert statement_text_cache_stats()["hits"] == 1


# --- shape must distinguish: dict ordering / IN length / limit & offset -----


def test_dict_key_order_is_part_of_the_shape():
    sql_a, _ = InsertQuery(None, "users", {"a": 1, "b": 2}).build()
    sql_b, _ = InsertQuery(None, "users", {"b": 2, "a": 1}).build()
    assert sql_a == "INSERT INTO users (a, b) VALUES (?, ?)"
    assert sql_b == "INSERT INTO users (b, a) VALUES (?, ?)"
    assert sql_a != sql_b
    assert statement_text_cache_stats()["misses"] == 2

    sql_c, _ = UpdateQuery(None, "users", {"a": 1, "b": 2}).where("id", "=", 1).build()
    sql_d, _ = UpdateQuery(None, "users", {"b": 2, "a": 1}).where("id", "=", 1).build()
    assert sql_c == "UPDATE users SET a = ?, b = ? WHERE id = ?"
    assert sql_d == "UPDATE users SET b = ?, a = ? WHERE id = ?"


def test_in_clause_length_is_part_of_the_shape():
    sql_2, params_2 = SelectQuery(None, "users").where("id", "IN", [1, 2]).build()
    sql_3, params_3 = SelectQuery(None, "users").where("id", "IN", [1, 2, 3]).build()
    assert sql_2 == "SELECT * FROM users WHERE id IN (?, ?)"
    assert sql_3 == "SELECT * FROM users WHERE id IN (?, ?, ?)"
    assert len(params_2) == 2 and len(params_3) == 3
    assert statement_text_cache_stats()["misses"] == 2


def test_limit_and_offset_are_part_of_the_shape():
    def base():
        return SelectQuery(None, "users").where("id", "=", 1)

    sql_a, _ = base().limit(10).build()
    sql_b, _ = base().limit(20).build()
    sql_c, _ = base().limit(10).offset(5).build()
    assert sql_a.endswith("LIMIT 10")
    assert sql_b.endswith("LIMIT 20")
    assert sql_c.endswith("LIMIT 10 OFFSET 5")
    assert len({sql_a, sql_b, sql_c}) == 3


# --- safety: caching never skips validation or leaks values ----------------


def test_values_are_never_spliced_into_cached_sql():
    evil = "x' OR '1'='1'; DROP TABLE users; --"
    sql, params = SelectQuery(None, "users").where("name", "=", evil).build()
    assert "?" in sql and evil not in sql
    assert evil in _values(params)
    # A second, different payload reuses the text but binds its own value.
    sql2, params2 = SelectQuery(None, "users").where("name", "=", "other").build()
    assert sql2 == sql
    assert "other" in _values(params2) and evil not in _values(params2)


def test_unary_operators_bind_no_values_and_share_text():
    sql_a, params_a = (
        SelectQuery(None, "users").where("deleted_at", "IS NULL", None).build()
    )
    sql_b, params_b = (
        SelectQuery(None, "users").where("deleted_at", "IS NULL", None).build()
    )
    assert sql_a == sql_b == "SELECT * FROM users WHERE deleted_at IS NULL"
    assert params_a == params_b == []
    assert statement_text_cache_stats()["hits"] == 1


def test_hostile_identifiers_still_rejected_after_warmup():
    # Warm a benign shape first.
    SelectQuery(None, "users").where("id", "=", 1).build()
    evil = "x' OR '1'='1'; DROP TABLE users; --"
    with pytest.raises(SQLInjectionError):
        SelectQuery(None, evil).build()
    with pytest.raises(SQLInjectionError):
        SelectQuery(None, "users").where(evil, "=", 1).build()
    with pytest.raises(SQLInjectionError):
        InsertQuery(None, "users", {evil: "v"}).build()
    # Failed compilations never reach the cache.
    assert statement_text_cache_stats()["misses"] == 1


def test_schema_registry_validation_still_runs_on_every_build():
    builder = QueryBuilder(None, enable_schema_validation=True)
    builder.register_schema("users", ["id", "name"])

    sql, _ = builder.table("users").select().where("name", "=", "alice").build()
    assert sql == "SELECT * FROM users WHERE name = ?"
    # Warmed shape is cached, but an unregistered column must still be refused
    # before any cache lookup could serve stale text.
    with pytest.raises(SchemaValidationError):
        builder.table("users").select().where("secret", "=", "x").build()
    with pytest.raises(SchemaValidationError):
        builder.table("users").select(["secret"]).build()


# --- cache hygiene & concurrency -------------------------------------------


def test_cache_is_bounded_and_reports_stats():
    stats = statement_text_cache_stats()
    assert stats["maxsize"] >= 1
    assert stats["hits"] == stats["misses"] == stats["evictions"] == 0
    # Build many distinct shapes; the cache must stay within its cap.
    for i in range(stats["maxsize"] + 40):
        SelectQuery(None, "users").where(f"c{i}", "=", i).build()
    after = statement_text_cache_stats()
    assert after["size"] <= after["maxsize"]
    assert after["evictions"] > 0


def test_concurrent_builds_share_one_compiled_text():
    def build(i):
        sql, params = (
            SelectQuery(None, "users")
            .where("id", "=", i)
            .where("name", "=", f"u{i}")
            .build()
        )
        return sql, _values(params)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(build, range(200)))

    expected = "SELECT * FROM users WHERE id = ? AND name = ?"
    assert all(sql == expected for sql, _ in results)
    assert [params for _, params in results] == [[i, f"u{i}"] for i in range(200)]
    # Racing misses collapse to a single stored value; the rest are hits.
    assert statement_text_cache_stats()["hits"] >= 190
