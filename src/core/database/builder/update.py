"""UPDATE query builder."""

from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel

import utils.logger as logger

from .base import TableQuery, Parameter
from .sql_safety import sql_assignments, sql_identifier
from .statement_cache import compiled_sql
from .where import assemble_where_clause, build_where_shape, where_params

T = TypeVar("T", bound=BaseModel)


class UpdateQuery(TableQuery):
    """Query builder for UPDATE statements."""

    def __init__(
        self,
        connection: Any,
        table_name: str,
        data: Dict[str, Any],
        db_type: str = "sqlite",
        schema_registry: Optional[Any] = None,
    ):
        super().__init__(connection, table_name, db_type, schema_registry)
        self.data = data
        self._where_conditions: List[tuple[str, str, Any]] = []

    def validate(self, model: Type[T]) -> "UpdateQuery":
        """Validate data against ``model`` and use the validated values.

        Unknown keys are rejected and Pydantic's coerced/normalized values
        replace the raw input, so no unvalidated field can reach the UPDATE.
        """
        super().validate(model)
        self.data = self._validated_data(model, self.data)
        logger.debug(f"Data validated successfully against {model.__name__}")
        return self

    def where(self, column: str, operator: str, value: Any) -> "UpdateQuery":
        """Add a WHERE condition."""
        self._where_conditions.append((column, operator, value))
        return self

    def build(self) -> tuple[str, list]:
        """Build the UPDATE query.

        SET column order comes from the data dict's key order, so the ordered
        column tuple is part of the shape key: two dicts with the same keys in a
        different order produce different (correctly cached) SQL text.
        """
        if not self._where_conditions:
            raise ValueError(
                "UPDATE requires at least one WHERE condition (safety feature)"
            )

        columns = list(self.data.keys())
        validated_columns = tuple(self._validate_identifiers_list(columns, "column"))

        if self.schema_registry:
            self.schema_registry.validate_columns(
                self.table_name, list(validated_columns)
            )

        where_shape = build_where_shape(
            self._where_conditions, self.schema_registry, self.table_name
        )

        shape = (
            "UPDATE",
            self.table_name,
            validated_columns,
            where_shape,
        )

        def compile_sql() -> str:
            assignments = sql_assignments(validated_columns)
            sql = (
                f"UPDATE {sql_identifier(self.table_name, 'table name')} "
                f"SET {assignments}"
            )
            sql += assemble_where_clause(where_shape)
            return sql

        sql = compiled_sql(shape, compile_sql)

        # SET values first (in dict key order), then WHERE values -- all rebuilt
        # from live values so reused SQL never reuses a stale parameter.
        params: List[Parameter] = [Parameter(self.data[col]) for col in columns]
        params.extend(where_params(self._where_conditions))

        return sql, params
