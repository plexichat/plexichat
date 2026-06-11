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

    def execute(self) -> int:
        """Execute the INSERT query and return the last inserted row ID.

        For SQLite, returns cursor.lastrowid. For PostgreSQL, returns 1 (row count)
        since lastrowid is not directly available without RETURNING clause.
        """
        sql, params = self.build()

        sanitized_params = [p.value if isinstance(p, Parameter) else p for p in params]
        logger.debug(f"Executing INSERT: {sql} with params: {sanitized_params}")

        try:
            if self.db_type == "sqlite":
                cursor = self.connection.cursor()
                param_values = [
                    p.value if isinstance(p, Parameter) else p for p in params
                ]
                cursor.execute(sql, param_values)
                self.connection.commit()
                lastrowid = cursor.lastrowid
                cursor.close()
                logger.debug(f"INSERT returned lastrowid: {lastrowid}")
                return lastrowid
            else:  # postgres
                cursor = self.connection.cursor()
                try:
                    param_values = [
                        p.value if isinstance(p, Parameter) else p for p in params
                    ]
                    from .. import dialect

                    pg_sql = dialect.convert_placeholders(sql, self.db_type)
                    cursor.execute(pg_sql, param_values)
                    self.connection.commit()
                    row_count = cursor.rowcount
                    cursor.close()
                    logger.debug(f"INSERT affected {row_count} rows")
                    return row_count
                finally:
                    cursor.close()
        except Exception as e:
            logger.error(f"INSERT execution failed: {str(e)}")
            try:
                self.connection.rollback()
            except Exception:
                pass
            raise
