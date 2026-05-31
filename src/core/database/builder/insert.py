"""INSERT query builder."""

from typing import Any, Dict, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

import utils.logger as logger

from .base import (
    TableQuery,
    Parameter,
    ValidationModelError,
)

T = TypeVar("T", bound=BaseModel)


class InsertQuery(TableQuery):
    """Query builder for INSERT statements."""

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

    def build(self) -> tuple[str, list]:
        """Build the INSERT query."""
        columns = list(self.data.keys())
        validated_columns = self._validate_identifiers_list(columns, "column")

        if self.schema_registry:
            self.schema_registry.validate_columns(self.table_name, validated_columns)

        placeholders = ", ".join(["?" for _ in columns])
        columns_str = ", ".join(validated_columns)
        sql = f"INSERT INTO {self.table_name} ({columns_str}) VALUES ({placeholders})"
        params = [Parameter(self.data[col]) for col in columns]

        return sql, params

    def validate(self, model: Type[T]) -> "InsertQuery":
        """Set validation model and validate data."""
        super().validate(model)

        try:
            model(**self.data)
            logger.debug(f"Data validated successfully against {model.__name__}")
        except ValidationError as e:
            raise ValidationModelError(f"Validation failed: {str(e)}")

        return self
