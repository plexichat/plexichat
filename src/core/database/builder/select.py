"""SELECT query builder."""

from typing import Any, List, Optional

from .base import TableQuery
from .where import build_where_clause


class SelectQuery(TableQuery):
    """Query builder for SELECT statements."""

    def __init__(
        self,
        connection: Any,
        table_name: str,
        columns: Optional[List[str]] = None,
        db_type: str = "sqlite",
        schema_registry: Optional[Any] = None,
    ):
        super().__init__(connection, table_name, db_type, schema_registry)
        self.columns = columns
        self._where_conditions: List[tuple[str, str, Any]] = []
        self._limit_value: Optional[int] = None
        self._offset_value: Optional[int] = None

    def where(self, column: str, operator: str, value: Any) -> "SelectQuery":
        """Add a WHERE condition."""
        self._where_conditions.append((column, operator, value))
        return self

    def limit(self, count: int) -> "SelectQuery":
        """Set LIMIT clause."""
        if not isinstance(count, int) or count < 0:
            raise ValueError(f"Limit must be non-negative integer, got {count}")
        self._limit_value = count
        return self

    def offset(self, count: int) -> "SelectQuery":
        """Set OFFSET clause."""
        if not isinstance(count, int) or count < 0:
            raise ValueError(f"Offset must be non-negative integer, got {count}")
        self._offset_value = count
        return self

    def build(self) -> tuple[str, list]:
        """Build the SELECT query."""
        # Build column list
        if self.columns:
            validated_columns = self._validate_identifiers_list(self.columns, "column")
            columns_str = ", ".join(validated_columns)
            if self.schema_registry:
                self.schema_registry.validate_columns(
                    self.table_name, validated_columns
                )
        else:
            columns_str = "*"

        sql = f"SELECT {columns_str} FROM {self.table_name}"

        # Add WHERE conditions using shared builder
        where_clause, params = build_where_clause(
            self._where_conditions, self.schema_registry, self.table_name
        )
        sql += where_clause

        # Add LIMIT and OFFSET
        if self._limit_value is not None:
            sql += f" LIMIT {self._limit_value}"
        if self._offset_value is not None:
            sql += f" OFFSET {self._offset_value}"

        return sql, params
