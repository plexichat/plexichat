"""
Base query classes for the type-safe SQL query builder.

Provides the foundational Query ABC and TableQuery intermediate class.
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel

import utils.logger as logger
from utils.logger import sanitize_data

from .protocol import QueryBuilderProtocol

T = TypeVar("T", bound=BaseModel)

if TYPE_CHECKING:
    from .schema_registry import SchemaRegistry


class QueryBuilderException(Exception):
    """Base exception for query builder errors."""

    pass


class SQLInjectionError(QueryBuilderException):
    """Raised when potentially malicious SQL is detected."""

    pass


class SchemaValidationError(QueryBuilderException):
    """Raised when table or column validation fails."""

    pass


class ValidationModelError(QueryBuilderException):
    """Raised when Pydantic validation fails."""

    pass


@dataclass
class Parameter:
    """Represents a query parameter for safe value passing."""

    value: Any

    def __repr__(self):
        return f"Parameter({self.value!r})"


class Query(ABC, QueryBuilderProtocol):
    """Base class for all query builders."""

    def __init__(self, connection: Any, db_type: str = "sqlite"):
        """Initialize query builder.

        Args:
            connection: Database connection object
            db_type: Database type ("sqlite" or "postgres")
        """
        self.connection = connection
        self.db_type = db_type
        self._validation_model: Optional[Type[BaseModel]] = None
        self._sql = ""
        self._params: List[Parameter] = []

    @abstractmethod
    def build(self) -> tuple[str, list]:
        """Build the SQL query and return (sql, params)."""
        pass

    def _validate_identifier(
        self, identifier: str, identifier_type: str = "column"
    ) -> str:
        """Validate and sanitize an identifier (table or column name)."""
        if not re.match(
            r"^[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)?$", identifier
        ):
            raise SQLInjectionError(
                f"Invalid {identifier_type} name: {identifier}. "
                f"Must start with letter/underscore and contain only alphanumeric/underscore characters."
            )
        return identifier

    def _validate_identifiers_list(
        self, identifiers: List[str], identifier_type: str = "column"
    ) -> List[str]:
        """Validate multiple identifiers."""
        return [
            self._validate_identifier(ident, identifier_type) for ident in identifiers
        ]

    def validate(self, model: Type[T]) -> "Query":
        """Set validation model for data validation before execution."""
        if not issubclass(model, BaseModel):
            raise ValueError(
                f"Validation model must be a Pydantic BaseModel, got {type(model)}"
            )
        self._validation_model = model
        return self

    def execute(self) -> Any:
        """Execute the query and return results."""
        sql, params = self.build()

        # Sanitize params for logging
        sanitized_params = [
            sanitize_data(p.value if isinstance(p, Parameter) else p) for p in params
        ]
        logger.debug(f"Executing query: {sql} with params: {sanitized_params}")

        try:
            if self.db_type == "sqlite":
                cursor = self.connection.cursor()
                param_values = [
                    p.value if isinstance(p, Parameter) else p for p in params
                ]
                cursor.execute(sql, param_values)

                if isinstance(self, SelectQuery):  # type: ignore  # Forward reference
                    results = cursor.fetchall()
                    logger.debug(f"Query returned {len(results)} rows")
                    return results
                else:
                    self.connection.commit()
                    row_count = cursor.rowcount
                    logger.debug(f"Query affected {row_count} rows")
                    cursor.close()
                    return row_count
            else:  # postgres
                cursor = self.connection.cursor()
                try:
                    param_values = [
                        p.value if isinstance(p, Parameter) else p for p in params
                    ]
                    from .. import dialect

                    pg_sql = dialect.convert_placeholders(sql, self.db_type)
                    cursor.execute(pg_sql, param_values)

                    if isinstance(self, SelectQuery):  # type: ignore
                        results = cursor.fetchall()
                        logger.debug(f"Query returned {len(results)} rows")
                        return results
                    else:
                        row_count = cursor.rowcount
                        self.connection.commit()
                        logger.debug(f"Query affected {row_count} rows")
                        return row_count
                finally:
                    cursor.close()
        except Exception as e:
            logger.error(f"Query execution failed: {str(e)}")
            try:
                self.connection.rollback()
            except Exception:
                pass
            raise


class TableQuery(Query):
    """Intermediate query class that represents a table selection."""

    def __init__(
        self,
        connection: Any,
        table_name: str,
        db_type: str = "sqlite",
        schema_registry: Optional["SchemaRegistry"] = None,
    ):
        super().__init__(connection, db_type)
        self.table_name = self._validate_identifier(table_name, "table")
        self.schema_registry = schema_registry

    def insert(self, data: Dict[str, Any]) -> "InsertQuery":  # type: ignore
        """Start building an INSERT query."""
        from .insert import InsertQuery

        if not isinstance(data, dict):
            raise ValueError(f"Data must be a dictionary, got {type(data)}")
        if not data:
            raise ValueError("Insert data cannot be empty")

        return InsertQuery(
            self.connection, self.table_name, data, self.db_type, self.schema_registry
        )

    def select(self, columns: Optional[List[str]] = None) -> "SelectQuery":  # type: ignore
        """Start building a SELECT query."""
        from .select import SelectQuery

        return SelectQuery(
            self.connection,
            self.table_name,
            columns,
            self.db_type,
            self.schema_registry,
        )

    def update(self, data: Dict[str, Any]) -> "UpdateQuery":  # type: ignore
        """Start building an UPDATE query."""
        from .update import UpdateQuery

        if not isinstance(data, dict):
            raise ValueError(f"Data must be a dictionary, got {type(data)}")
        if not data:
            raise ValueError("Update data cannot be empty")

        return UpdateQuery(
            self.connection, self.table_name, data, self.db_type, self.schema_registry
        )

    def delete(self) -> "DeleteQuery":  # type: ignore
        """Start building a DELETE query."""
        from .delete import DeleteQuery

        return DeleteQuery(
            self.connection, self.table_name, self.db_type, self.schema_registry
        )

    def build(self) -> tuple[str, list]:
        """Not implemented for TableQuery."""
        raise NotImplementedError(
            "TableQuery cannot be executed directly. Use insert(), select(), update(), or delete()."
        )


# Late imports to avoid circular dependencies
from .insert import InsertQuery  # noqa: E402, F811
from .select import SelectQuery  # noqa: E402, F811
from .update import UpdateQuery  # noqa: E402, F811
from .delete import DeleteQuery  # noqa: E402, F811
