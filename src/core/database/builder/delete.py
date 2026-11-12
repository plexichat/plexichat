"""DELETE query builder."""

from typing import Any, List, Optional

from .base import TableQuery, Parameter
from .where import build_where_clause

_DELETE_FROM = "DELETE FROM {table}"


class DeleteQuery(TableQuery):
    """Query builder for DELETE statements."""

    def __init__(
        self,
        connection: Any,
        table_name: str,
        db_type: str = "sqlite",
        schema_registry: Optional[Any] = None,
    ):
        super().__init__(connection, table_name, db_type, schema_registry)
        self._where_conditions: List[tuple[str, str, Any]] = []

    def where(self, column: str, operator: str, value: Any) -> "DeleteQuery":
        """Add a WHERE condition."""
        self._where_conditions.append((column, operator, value))
        return self

    def build(self) -> tuple[str, list]:
        """Build the DELETE query."""
        if not self._where_conditions:
            raise ValueError(
                "DELETE requires at least one WHERE condition (safety feature)"
            )

        # TableQuery validates ``table_name`` as a bare identifier; the
        # template keeps the statement out of the f-string form.
        sql = _DELETE_FROM.format(table=self.table_name)
        params: List[Parameter] = []

        # Add WHERE conditions using shared builder
        where_clause, where_params = build_where_clause(
            self._where_conditions, self.schema_registry, self.table_name
        )
        sql += where_clause
        params.extend(where_params)

        return sql, params
