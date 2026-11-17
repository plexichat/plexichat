"""Interpolated numbers are bounded and non-finite values are rejected.

LIMIT/OFFSET literals, IN-clause placeholder counts and clone batch sizes are
the only numbers that reach SQL text. Each is capped; ``nan``/``inf`` are
rejected (instead of becoming an unbounded statement or an OverflowError deep
in a driver). Values that stay as bound parameters get the same finite check.
"""

import math
from typing import cast

import pytest

from src.core.database.core import Database
from src.core.database.builder import QueryBuilder
from src.core.database.builder.sql_safety import (
    MAX_PLACEHOLDERS,
    bounded_sql_int,
    sql_finite_float,
    sql_limit,
    sql_offset,
    sql_placeholders,
)
from src.core.migrations.cloner import DataCloner
from src.core.search.indexer.postgres import PostgresIndexer
from src.core.search.indexer.sqlite_fts import _bounded_page
from src.core.security.hardening import HardeningViolation

pytestmark = [pytest.mark.unit, pytest.mark.security]


class _FakeDB:
    def __init__(self, path, dbtype="sqlite"):
        self.type = dbtype
        self.config = {"type": dbtype, "path": path}
        self.selects = []

    def execute(self, query, params=None):
        return None

    def fetch_one(self, query, params=None):
        if "websearch_to_tsquery" in query:
            return {"?": 1}
        return {"count": 0}

    def fetch_all(self, query, params=None):
        self.selects.append((query, params))
        return []


@pytest.mark.parametrize(
    "bad",
    [-1, -(10**9), 10**9, 10**30, True, float("nan"), float("inf"), float("-inf")],
)
def test_sql_limit_rejects_out_of_range_and_non_finite(bad):
    with pytest.raises(HardeningViolation):
        sql_limit(bad)


@pytest.mark.parametrize("bad", [-1, 10**9, float("nan"), float("inf"), "abc"])
def test_sql_offset_rejects_out_of_range_and_non_finite(bad):
    with pytest.raises(HardeningViolation):
        sql_offset(bad)


def test_sql_limit_offset_accept_valid():
    assert sql_limit(25) == 25
    assert sql_offset(0) == 0
    assert sql_limit(1_000_000) == 1_000_000


def test_placeholder_count_is_bounded():
    assert sql_placeholders(3) == "?, ?, ?"
    assert sql_placeholders(0) == ""
    with pytest.raises(HardeningViolation):
        sql_placeholders(MAX_PLACEHOLDERS + 1)
    with pytest.raises(HardeningViolation):
        sql_placeholders(float("nan"))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_finite_helpers_reject_non_finite(bad):
    with pytest.raises(HardeningViolation):
        bounded_sql_int(bad, "n", minimum=0, maximum=100)
    with pytest.raises(HardeningViolation):
        sql_finite_float(bad, "n")
    assert not math.isfinite(bad)  # documents the input shape


def test_builder_bounds_limit_literal_in_sql():
    builder = QueryBuilder(object(), db_type="sqlite")
    sql, params = builder.table("users").select().limit(25).offset(5).build()
    assert "LIMIT 25" in sql
    assert "OFFSET 5" in sql
    assert params == []


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), 10**9, -1])
def test_builder_rejects_non_finite_and_unbounded_pages(bad):
    builder = QueryBuilder(object(), db_type="sqlite")
    with pytest.raises(ValueError):
        builder.table("users").select().limit(bad).build()
    with pytest.raises(ValueError):
        builder.table("users").select().offset(bad).build()


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), 0, -1, 100_001])
def test_cloner_batch_size_is_bounded(bad):
    cloner = DataCloner(
        cast(Database, _FakeDB("/tmp/a.db")), cast(Database, _FakeDB("/tmp/b.db"))
    )
    with pytest.raises(HardeningViolation):
        cloner.batch_size = bad
    assert cloner.batch_size == 1000  # unchanged after rejection


@pytest.mark.parametrize("bad", [float("inf"), float("nan")])
def test_fts_bounded_page_rejects_non_finite(bad):
    with pytest.raises(HardeningViolation):
        _bounded_page(bad, 0)
    with pytest.raises(HardeningViolation):
        _bounded_page(25, bad)


def test_postgres_in_clause_placeholder_count_is_bounded():
    db = _FakeDB("/tmp/a.db", dbtype="postgres")
    indexer = PostgresIndexer(db)
    indexer.initialize()
    ids = list(range(1, 6))
    indexer.search_messages("hello", conversation_ids=ids)

    sql, params = db.selects[-1]
    assert "conversation_id IN (?, ?, ?, ?, ?)" in sql
    assert tuple(params[-2:]) == (25, 0)  # bounded limit/offset are trailing params
