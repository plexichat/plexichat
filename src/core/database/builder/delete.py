"""DELETE query builder."""

from typing import Any, List, Optional

from .base import TableQuery, Parameter
from .sql_safety import sql_identifier
from .statement_cache import compiled_sql
from .where import assemble_where_clause, build_where_shape, where_params

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
        """Build the DELETE query (text cached by shape, values always bound)."""
        if not self._where_conditions:
            raise ValueError(
                "DELETE requires at least one WHERE condition (safety feature)"
            )

        where_shape = build_where_shape(
            self._where_conditions, self.schema_registry, self.table_name
        )

        shape = ("DELETE", self.table_name, where_shape)

        def compile_sql() -> str:
            # The statement template keeps the SQL out of an f-string; the name
            # is funnelled through the shared identifier rule before it lands.
            sql = _DELETE_FROM.format(
                table=sql_identifier(self.table_name, "table name")
            )
            sql += assemble_where_clause(where_shape)
            return sql

        sql = compiled_sql(shape, compile_sql)
        params: List[Parameter] = where_params(self._where_conditions)
        return sql, params
