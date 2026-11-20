"""WHERE clause helper for query builder.

Provides shared WHERE logic used by Select, Update, and Delete queries.
All SQL text is assembled by ``sql_safety``; this module only supplies the
validated column list and the bound value parameters.

Assembly is split into three steps so builders can cache the *text* by shape
while still validating on every build and rebinding values every call:

* :func:`build_where_shape` validates conditions and returns a value-free key;
* :func:`assemble_where_clause` turns that key into SQL text (cached); and
* :func:`where_params` builds fresh bound parameters from the live values.
"""

from typing import Any, List, Optional, Tuple

from src.core.security.hardening import HardeningViolation

from .base import Parameter, SQLInjectionError
from .sql_safety import (
    VALID_OPERATORS,
    VALID_OPERATORS_UNARY,
    sql_condition,
    sql_identifier,
)

ALL_VALID_OPERATORS = VALID_OPERATORS | VALID_OPERATORS_UNARY

#: ``(safe_column, operator, number_of_bound_values)`` per condition.  No value
#: data is retained, so a shape is safe to use as a statement-cache key.
WhereShape = Tuple[Tuple[str, str, int], ...]


def build_where_shape(
    conditions: List[tuple[str, str, Any]],
    schema_registry: Any = None,
    table_name: Optional[str] = None,
) -> WhereShape:
    """Validate ``conditions`` and return a hashable, value-free WHERE shape.

    The full validation path (identifier hardening, operator allow-list, IN
    payload type and optional schema-column check) runs on *every* build.  That
    is what makes caching the assembled text safe: an invalid query is rejected
    here before a builder ever looks up its shape in the statement cache.
    """
    shape: List[Tuple[str, str, int]] = []
    where_columns: List[str] = []

    for column, operator, value in conditions:
        # Identifier validation is centralized in require_identifier; keep the
        # builder's public exception type for callers that catch it.
        try:
            safe_column = sql_identifier(column, "column")
        except HardeningViolation as exc:
            raise SQLInjectionError(f"Invalid column name: {column}") from exc

        where_columns.append(safe_column)
        op = operator.upper() if isinstance(operator, str) else ""

        if op in VALID_OPERATORS_UNARY:
            shape.append((safe_column, op, 0))
        elif op == "IN":
            if not isinstance(value, (list, tuple)):
                raise ValueError(f"IN operator requires list/tuple, got {type(value)}")
            shape.append((safe_column, op, len(value)))
        elif op in VALID_OPERATORS:
            shape.append((safe_column, op, 1))
        else:
            raise ValueError(
                f"Invalid operator: {operator}. Must be one of {ALL_VALID_OPERATORS}"
            )

    # Validate columns against schema if registry is set
    if schema_registry and table_name:
        schema_registry.validate_columns(table_name, where_columns)

    return tuple(shape)


def assemble_where_clause(shape: WhereShape) -> str:
    """Assemble WHERE text from a validated shape (pure; safe to cache)."""
    where_parts: List[str] = []
    for safe_column, op, value_count in shape:
        where_parts.append(sql_condition(safe_column, op, value_count))

    if not where_parts:
        return ""

    return " WHERE " + " AND ".join(where_parts)


def where_params(conditions: List[tuple[str, str, Any]]) -> List[Parameter]:
    """Build bound parameters from ``conditions`` with their live values.

    Called on every build (never cached) so two queries with the same shape but
    different values share SQL text while keeping values as placeholders.
    """
    params: List[Parameter] = []
    for column, operator, value in conditions:
        op = operator.upper() if isinstance(operator, str) else ""
        if op == "IN":
            params.extend(Parameter(v) for v in value)
        elif op in VALID_OPERATORS:
            params.append(Parameter(value))
        # Unary operators (IS NULL / IS NOT NULL) bind no value.
    return params


def build_where_clause(
    conditions: List[tuple[str, str, Any]],
    schema_registry: Any = None,
    table_name: Optional[str] = None,
) -> tuple[str, List[Parameter]]:
    """Validate, assemble and bind a WHERE clause in one call.

    Kept for backwards compatibility with callers outside the builders.  The
    builders use the granular helpers above so they can cache the text by shape.
    """
    shape = build_where_shape(conditions, schema_registry, table_name)
    return assemble_where_clause(shape), where_params(conditions)
