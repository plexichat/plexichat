"""UPDATE query builder."""

from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel

import utils.logger as logger

from .base import TableQuery, Parameter
from .sql_safety import sql_assignments, sql_identifier
from .where import build_where_clause

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
        """Build the UPDATE query."""
        if not self._where_conditions:
            raise ValueError(
                "UPDATE requires at least one WHERE condition (safety feature)"
            )

        columns = list(self.data.keys())
        validated_columns = self._validate_identifiers_list(columns, "column")

        if self.schema_registry:
            self.schema_registry.validate_columns(self.table_name, validated_columns)

        assignments = sql_assignments(validated_columns)
        sql = (
            f"UPDATE {sql_identifier(self.table_name, 'table name')} SET {assignments}"
        )
        params: List[Parameter] = [Parameter(self.data[col]) for col in columns]

        # Add WHERE conditions using shared builder
        where_clause, where_params = build_where_clause(
            self._where_conditions, self.schema_registry, self.table_name
        )
        sql += where_clause
        params.extend(where_params)

        return sql, params
