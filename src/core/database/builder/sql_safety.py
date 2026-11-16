"""Single choke point for SQL text that cannot be bound as a parameter.

Only object names (tables/columns/indexes) and a handful of numeric fragments
(``LIMIT``/``OFFSET``, placeholder counts) are ever concatenated into a SQL
statement; every actual *value* is a DB-API placeholder.  This module owns all
of that concatenation so that:

* callers cannot invent a new ad-hoc validation rule (they call one of the
  helpers below, which delegate to :mod:`src.core.security.hardening`); and
* the static guard in ``src/tests/database/test_builder_sql_static_guard.py``
  has a single list of sanctioned interpolations it can check callers against.

Names go through :func:`require_identifier` (plain identifier, 63-char bound).
A dotted name is validated one segment at a time so ``schema.table`` keeps
working without weakening the rule.  Numbers go through :func:`bounded_int`
with an explicit finite check, so ``nan``/``inf`` and giant ints cannot reach a
statement planner as a SQL literal.

What cannot be parameterized (and therefore lives here):

* object names in DDL/PRAGMA and in the target/column clauses of DML — a bind
  marker stands for a value, never for an identifier;
* ``LIMIT``/``OFFSET`` literals (some drivers reject a bound limit) and the
  count-derived ``?, ?, ...`` list of an ``IN`` clause;
* constant SQL keywords/operators from the fixed allow-lists below.

Everything else is a value and must be bound.  In particular, values that used
to be written into a SQL string literal (like the table-name argument to
``pg_get_serial_sequence()`` in the migration cloner) are now bind parameters.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

from src.core.security.hardening import (
    HardeningViolation,
    bounded_int,
    require_identifier,
)

#: Largest LIMIT/OFFSET literal we will put into a statement.  Anything larger
#: turns a bounded query into a full-table read and can overflow DB drivers.
MAX_SQL_LITERAL = 1_000_000

#: Conservative ceiling on the number of ``?`` markers in one IN clause (kept
#: below SQLite's historical variable limit and every common driver limit).
MAX_PLACEHOLDERS = 32_766

VALID_OPERATORS = frozenset({"=", "<>", "<", ">", "<=", ">=", "LIKE", "IN"})
VALID_OPERATORS_UNARY = frozenset({"IS NULL", "IS NOT NULL"})


def _finite_int(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    """Bounded integer that also rejects booleans and non-finite floats."""
    if isinstance(value, bool):
        raise HardeningViolation(f"{field} must be an integer, not a boolean")
    if isinstance(value, float) and not math.isfinite(value):
        raise HardeningViolation(f"{field} must be a finite number")
    return bounded_int(value, field, minimum=minimum, maximum=maximum)


def bounded_sql_int(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    """Public finite/bounded integer for callers that interpolate numbers."""
    return _finite_int(value, field, minimum=minimum, maximum=maximum)


def sql_finite_float(value: Any, field: str = "value") -> float:
    """Coerce to float, rejecting ``nan``/``inf`` (e.g. a crafted cursor)."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise HardeningViolation(f"{field} must be a number") from exc
    if not math.isfinite(number):
        raise HardeningViolation(f"{field} must be a finite number")
    return number


def sql_identifier(
    value: Any, field: str = "identifier", quote: str | None = None
) -> str:
    """Validate a SQL object name that must be interpolated, not bound.

    ``quote`` optionally wraps the *validated* name in ``"`` (Postgres) or
    `` ` `` (SQLite) for dialects that require it; since the name is already a
    plain identifier, the quote characters cannot be smuggled into the SQL.
    """
    if not isinstance(value, str):
        # Let the central helper produce the (ValueError-derived) rejection.
        return require_identifier(value, field)
    parts = value.split(".")
    if not parts or any(not part for part in parts):
        raise HardeningViolation(f"{field} is not a valid SQL identifier")
    for part in parts:
        require_identifier(part, field)
    if quote is None:
        return value
    if quote not in ('"', "`"):
        raise HardeningViolation("unsupported SQL identifier quote")
    return f"{quote}{value}{quote}"


def sql_identifier_list(values: Iterable[Any], field: str = "identifier") -> str:
    """Comma-separated, individually validated list of column names."""
    return ", ".join(sql_identifier(value, field) for value in values)


def sql_placeholders(count: Any, field: str = "value count") -> str:
    """Bounded ``?, ?, ...`` list for an ``IN`` clause."""
    n = _finite_int(count, field, minimum=0, maximum=MAX_PLACEHOLDERS)
    return ", ".join(["?"] * n)


def sql_assignments(columns: Iterable[Any], field: str = "column") -> str:
    """Bounded ``col = ?, ...`` list for a ``SET`` clause."""
    return ", ".join(f"{sql_identifier(c, field)} = ?" for c in columns)


def sql_limit(value: Any, field: str = "LIMIT") -> int:
    """Finite, bounded integer safe to interpolate after ``LIMIT``."""
    return _finite_int(value, field, minimum=0, maximum=MAX_SQL_LITERAL)


def sql_offset(value: Any, field: str = "OFFSET") -> int:
    """Finite, bounded integer safe to interpolate after ``OFFSET``."""
    return _finite_int(value, field, minimum=0, maximum=MAX_SQL_LITERAL)


def sql_operator(value: Any) -> str:
    """Return the canonical form of an allow-listed WHERE operator."""
    if not isinstance(value, str):
        raise HardeningViolation("SQL operator must be a string")
    operator = value.upper()
    if operator not in VALID_OPERATORS | VALID_OPERATORS_UNARY:
        raise HardeningViolation(f"invalid SQL operator: {value!r}")
    return operator


def sql_condition(column: Any, operator: Any, value_count: int = 0) -> str:
    """Build a single validated WHERE fragment (values stay placeholders)."""
    col = sql_identifier(column, "column")
    op = sql_operator(operator)
    if op in VALID_OPERATORS_UNARY:
        return f"{col} {op}"
    if op == "IN":
        return f"{col} IN ({sql_placeholders(value_count)})"
    return f"{col} {op} ?"
