"""WHERE clause helper for query builder.

Provides shared WHERE logic used by Select, Update, and Delete queries.
All SQL text is assembled by ``sql_safety``; this module only supplies the
validated column list and the bound value parameters.
"""

from typing import Any, List, Optional

from src.core.security.hardening import HardeningViolation

from .base import Parameter, SQLInjectionError
from .sql_safety import (
    VALID_OPERATORS,
    VALID_OPERATORS_UNARY,
    sql_condition,
    sql_identifier,
)

ALL_VALID_OPERATORS = VALID_OPERATORS | VALID_OPERATORS_UNARY


def build_where_clause(
    conditions: List[tuple[str, str, Any]],
    schema_registry: Any = None,
    table_name: Optional[str] = None,
) -> tuple[str, List[Parameter]]:
    """Build a SQL WHERE clause from a list of conditions.

    Args:
        conditions: List of (column, operator, value) tuples
        schema_registry: Optional SchemaRegistry for column validation
        table_name: Required if schema_registry is provided

    Returns:
        Tuple of (where_clause_string, list_of_parameters)
    """
    where_parts = []
    params: List[Parameter] = []
    where_columns = []

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
            where_parts.append(sql_condition(safe_column, op))
        elif op == "IN":
            if not isinstance(value, (list, tuple)):
                raise ValueError(f"IN operator requires list/tuple, got {type(value)}")
            where_parts.append(sql_condition(safe_column, op, len(value)))
            params.extend(Parameter(v) for v in value)
        elif op in VALID_OPERATORS:
            where_parts.append(sql_condition(safe_column, op))
            params.append(Parameter(value))
        else:
            raise ValueError(
                f"Invalid operator: {operator}. Must be one of {ALL_VALID_OPERATORS}"
            )

    # Validate columns against schema if registry is set
    if schema_registry and table_name:
        schema_registry.validate_columns(table_name, where_columns)

    if not where_parts:
        return "", []

    return " WHERE " + " AND ".join(where_parts), params
