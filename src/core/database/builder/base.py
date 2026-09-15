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
    from .delete import DeleteQuery
    from .insert import InsertQuery
    from .schema_registry import SchemaRegistry
    from .select import SelectQuery
    from .update import UpdateQuery


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
        # Optional back-reference to the owning Database facade so write
        # queries can invalidate its internal query-result cache exactly
        # like ``Database.execute()`` does. Set by ``QueryBuilder``.
        self.database: Any = None
        self._validation_model: Optional[Type[BaseModel]] = None
        self._sql = ""
        self._params: List[Parameter] = []

    def _invalidate_database_query_cache(self) -> None:
        """Clear the Database facade's query-result cache after a write.

        Builder writes run on the raw thread-local connection and would
        otherwise leave stale fetch_one/fetch_all results cached for up to
        ``_query_cache_ttl`` (read-your-write violation).
        """
        invalidator = getattr(self.database, "_invalidate_query_cache", None)
        if callable(invalidator):
            invalidator()

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
        # Imported here (not at module level) to avoid a circular import:
        # select.py imports from this module.
        from .select import SelectQuery

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

                if isinstance(self, SelectQuery):  # Forward reference
                    results = cursor.fetchall()
                    logger.debug(f"Query returned {len(results)} rows")
                    return results
                else:
                    self.connection.commit()
                    row_count = cursor.rowcount
                    logger.debug(f"Query affected {row_count} rows")
                    cursor.close()
                    self._invalidate_database_query_cache()
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

                    if isinstance(self, SelectQuery):
                        results = cursor.fetchall()
                        logger.debug(f"Query returned {len(results)} rows")
                        return results
                    else:
                        row_count = cursor.rowcount
                        self.connection.commit()
                        logger.debug(f"Query affected {row_count} rows")
                        self._invalidate_database_query_cache()
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

    def insert(self, data: Dict[str, Any]) -> "InsertQuery":
        """Start building an INSERT query."""
        from .insert import InsertQuery

        if not isinstance(data, dict):
            raise ValueError(f"Data must be a dictionary, got {type(data)}")
        if not data:
            raise ValueError("Insert data cannot be empty")

        query = InsertQuery(
            self.connection, self.table_name, data, self.db_type, self.schema_registry
        )
        query.database = self.database
        return query

    def select(self, columns: Optional[List[str]] = None) -> "SelectQuery":
        """Start building a SELECT query."""
        from .select import SelectQuery

        query = SelectQuery(
            self.connection,
            self.table_name,
            columns,
            self.db_type,
            self.schema_registry,
        )
        query.database = self.database
        return query

    def update(self, data: Dict[str, Any]) -> "UpdateQuery":
        """Start building an UPDATE query."""
        from .update import UpdateQuery

        if not isinstance(data, dict):
            raise ValueError(f"Data must be a dictionary, got {type(data)}")
        if not data:
            raise ValueError("Update data cannot be empty")

        query = UpdateQuery(
            self.connection, self.table_name, data, self.db_type, self.schema_registry
        )
        query.database = self.database
        return query

    def delete(self) -> "DeleteQuery":
        """Start building a DELETE query."""
        from .delete import DeleteQuery

        query = DeleteQuery(
            self.connection, self.table_name, self.db_type, self.schema_registry
        )
        query.database = self.database
        return query

    def build(self) -> tuple[str, list]:
        """Not implemented for TableQuery."""
        raise NotImplementedError(
            "TableQuery cannot be executed directly. Use insert(), select(), update(), or delete()."
        )
