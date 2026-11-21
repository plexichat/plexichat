"""Regression: validated SQL fragments must not be caller-derived str objects.

``sql_identifier`` used to validate ``value.split('.')`` but then interpolate the
original ``value`` object. A ``str`` subclass can lie in ``split()`` (passing a
harmless segment to the validator while its real characters contain SQL) or in
``__format__``/``__str__`` (valid characters, but a different rendering when the
f-string is built). ``sql_operator`` had the same flaw: it returned the result of
``value.upper()``, which can be a ``str`` subclass whose ``__format__`` emits SQL.

Both helpers must now return a fresh, plain ``str`` built from the exact text
that was validated, so no override on the caller's object can change the SQL.
"""

import pytest

from src.core.database.builder import QueryBuilder
from src.core.database.builder.sql_safety import (
    sql_condition,
    sql_identifier,
    sql_operator,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]


class LyingSplit(str):
    """Validates as ``users`` but really contains an injected table name."""

    def split(self, sep=None, maxsplit=-1):
        return ["users"]


class LyingFormat(str):
    """Validates as ``users`` but renders as injected SQL in an f-string."""

    def __format__(self, spec):
        return "users; DROP TABLE accounts; --"


class LyingOperator(str):
    """Compares/hashes as ``=`` but renders as an injected WHERE fragment."""

    def __new__(cls, display):
        return super().__new__(cls, "=")

    def upper(self):
        return self

    def __format__(self, spec):
        return self._display

    def __init__(self, display):
        self._display = display


def test_identifier_lying_split_cannot_smuggle_sql_into_table():
    sql, _ = (
        QueryBuilder(object(), db_type="sqlite")
        .table(LyingSplit("users; DROP TABLE accounts; --"))
        .select()
        .build()
    )
    assert sql == "SELECT * FROM users"
    assert "DROP" not in sql


def test_identifier_lying_format_cannot_smuggle_sql_into_table():
    sql, _ = (
        QueryBuilder(object(), db_type="sqlite")
        .table(LyingFormat("users"))
        .select()
        .build()
    )
    assert sql == "SELECT * FROM users"
    assert "DROP" not in sql


def test_operator_lying_format_cannot_smuggle_sql_into_where():
    sql, params = (
        QueryBuilder(object(), db_type="sqlite")
        .table("users")
        .select()
        .where("id", LyingOperator("= 1 OR 1=1 --"), 7)
        .build()
    )
    assert sql == "SELECT * FROM users WHERE id = ?"
    assert "OR 1=1" not in sql
    assert [p.value for p in params] == [7]


def test_choke_points_return_plain_canonical_strings():
    # The returned object must be a builtin str, not the caller's subclass.
    assert type(sql_identifier(LyingSplit("users; DROP TABLE x"), "table")) is str
    assert sql_identifier(LyingSplit("users; DROP TABLE x"), "table") == "users"
    assert type(sql_operator(LyingOperator("anything"))) is str
    assert sql_operator(LyingOperator("anything")) == "="
    assert sql_condition("id", LyingOperator("= 1 OR 1=1 --")) == "id = ?"


def test_valid_identifiers_and_operators_still_work():
    """The hardening must not reject legitimate dotted names or operators."""
    assert sql_identifier("public.users", "table") == "public.users"
    assert sql_identifier("users", "table", quote='"') == '"users"'
    assert sql_condition("name", "like") == "name LIKE ?"
    assert sql_condition("name", "IS NOT NULL") == "name IS NOT NULL"
    assert sql_condition("id", "in", 2) == "id IN (?, ?)"
