"""WHERE clause helper for query builder.

Provides shared WHERE logic used by Select, Update, and Delete queries.
"""

from typing import Any, List, Optional

from .base import Parameter, SQLInjectionError


VALID_OPERATORS = {
    "=",
    "<>",
    "<",
    ">",
    "<=",
    ">=",
    "LIKE",
    "IN",
}
VALID_OPERATORS_UNARY = {"IS NULL", "IS NOT NULL"}
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
        # Validate identifier
        if (
            not isinstance(column, str)
            or not column.replace("_", "").replace(".", "").isalnum()
        ):
            raise SQLInjectionError(f"Invalid column name: {column}")

        where_columns.append(column)

        if operator.upper() in VALID_OPERATORS_UNARY:
            where_parts.append(f"{column} {operator}")
        elif operator.upper() == "IN":
            if not isinstance(value, (list, tuple)):
                raise ValueError(f"IN operator requires list/tuple, got {type(value)}")
            placeholders = ", ".join(["?" for _ in value])
            where_parts.append(f"{column} IN ({placeholders})")
            params.extend(Parameter(v) for v in value)
        elif operator.upper() in VALID_OPERATORS:
            where_parts.append(f"{column} {operator} ?")
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
