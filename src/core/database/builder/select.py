"""SELECT query builder."""

from typing import Any, List, Optional

from .base import TableQuery
from .sql_safety import sql_identifier, sql_identifier_list, sql_limit, sql_offset
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
        """Set LIMIT clause, rejecting negative, non-finite and huge values."""
        self._limit_value = sql_limit(count, "limit")
        return self

    def offset(self, count: int) -> "SelectQuery":
        """Set OFFSET clause, rejecting negative, non-finite and huge values."""
        self._offset_value = sql_offset(count, "offset")
        return self

    def build(self) -> tuple[str, list]:
        """Build the SELECT query."""
        # Build column list; every column is validated through the shared rule
        # (mapped to SQLInjectionError by _validate_identifiers_list).
        if self.columns:
            validated_columns = self._validate_identifiers_list(self.columns, "column")
            columns_str = sql_identifier_list(validated_columns, "column")
            if self.schema_registry:
                self.schema_registry.validate_columns(
                    self.table_name, validated_columns
                )
        else:
            columns_str = "*"

        sql = (
            f"SELECT {columns_str} FROM {sql_identifier(self.table_name, 'table name')}"
        )

        # Add WHERE conditions using shared builder
        where_clause, params = build_where_clause(
            self._where_conditions, self.schema_registry, self.table_name
        )
        sql += where_clause

        # Add LIMIT and OFFSET (already bounded/validated by the setters)
        if self._limit_value is not None:
            limit_value = sql_limit(self._limit_value, "limit")
            sql += f" LIMIT {limit_value}"
        if self._offset_value is not None:
            offset_value = sql_offset(self._offset_value, "offset")
            sql += f" OFFSET {offset_value}"

        return sql, params
