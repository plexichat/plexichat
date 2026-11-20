"""SELECT query builder."""

from typing import Any, List, Optional

from .base import TableQuery
from .sql_safety import sql_identifier, sql_identifier_list, sql_limit, sql_offset
from .statement_cache import compiled_sql
from .where import assemble_where_clause, build_where_shape, where_params


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
        """Build the SELECT query.

        Validation always runs; only the pure text assembly is cached by shape
        and the bound parameters are rebuilt from the live values each call.
        """
        # Build column list; every column is validated through the shared rule
        # (mapped to SQLInjectionError by _validate_identifiers_list).
        validated_columns: Optional[tuple] = None
        if self.columns:
            validated_columns = tuple(
                self._validate_identifiers_list(self.columns, "column")
            )
            if self.schema_registry:
                self.schema_registry.validate_columns(
                    self.table_name, list(validated_columns)
                )

        where_shape = build_where_shape(
            self._where_conditions, self.schema_registry, self.table_name
        )

        # Shape is value-free: identifiers, operators, IN lengths and the
        # bounded LIMIT/OFFSET integers.  Different values share one SQL text.
        shape = (
            "SELECT",
            self.table_name,
            validated_columns,
            where_shape,
            self._limit_value,
            self._offset_value,
        )

        def compile_sql() -> str:
            if validated_columns is not None:
                columns_str = sql_identifier_list(validated_columns, "column")
            else:
                columns_str = "*"

            sql = (
                f"SELECT {columns_str} "
                f"FROM {sql_identifier(self.table_name, 'table name')}"
            )
            sql += assemble_where_clause(where_shape)

            # LIMIT/OFFSET are already bounded/validated by the setters.
            if self._limit_value is not None:
                sql += f" LIMIT {sql_limit(self._limit_value, 'limit')}"
            if self._offset_value is not None:
                sql += f" OFFSET {sql_offset(self._offset_value, 'offset')}"
            return sql

        sql = compiled_sql(shape, compile_sql)
        params = where_params(self._where_conditions)
        return sql, params
