"""Main QueryBuilder class - entry point for building queries."""

from typing import Any, Dict, List

import utils.logger as logger

from .base import TableQuery
from .schema_registry import SchemaRegistry


class QueryBuilder:
    """Main query builder class - entry point for building queries."""

    def __init__(
        self,
        connection: Any,
        db_type: str = "sqlite",
        enable_schema_validation: bool = False,
    ):
        """Initialize query builder.

        Args:
            connection: Database connection object
            db_type: Database type ("sqlite" or "postgres")
            enable_schema_validation: Enable schema registry validation
        """
        self.connection = connection
        self.db_type = db_type
        self.schema_registry = SchemaRegistry() if enable_schema_validation else None
        logger.debug(
            f"QueryBuilder initialized with db_type={db_type}, schema_validation={enable_schema_validation}"
        )

    def table(self, table_name: str) -> TableQuery:
        """Start building a query for a table.

        Args:
            table_name: Name of the table

        Returns:
            TableQuery instance for method chaining
        """
        return TableQuery(
            self.connection, table_name, self.db_type, self.schema_registry
        )

    def register_schema(self, table_name: str, columns: List[str]) -> None:
        """Register a table schema."""
        if self.schema_registry is None:
            raise ValueError(
                "Schema registry not enabled. Initialize QueryBuilder with enable_schema_validation=True"
            )
        self.schema_registry.register_table(table_name, columns)

    def register_schemas(self, schemas: Dict[str, List[str]]) -> None:
        """Register multiple table schemas."""
        if self.schema_registry is None:
            raise ValueError(
                "Schema registry not enabled. Initialize QueryBuilder with enable_schema_validation=True"
            )
        self.schema_registry.register_tables(schemas)
