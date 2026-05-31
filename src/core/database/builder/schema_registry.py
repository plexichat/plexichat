"""Schema registry for table metadata validation."""

from typing import Any, Dict, List, Optional

import utils.logger as logger

from .base import SchemaValidationError, QueryBuilderProtocol


class SchemaRegistry(QueryBuilderProtocol):
    """Registry for table schemas and metadata validation."""

    def __init__(self):
        """Initialize schema registry."""
        self.schemas: Dict[str, Dict[str, Any]] = {}
        logger.debug("SchemaRegistry initialized")

    def register_table(
        self,
        table_name: str,
        columns: List[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Register a table schema."""
        self.schemas[table_name] = {"columns": columns, "metadata": metadata or {}}
        logger.debug(
            f"Registered schema for table '{table_name}' with columns: {columns}"
        )

    def register_tables(self, schemas: Dict[str, List[str]]) -> None:
        """Register multiple table schemas."""
        for table_name, columns in schemas.items():
            self.register_table(table_name, columns)

    def validate_table(self, table_name: str) -> bool:
        """Check if table is registered."""
        if table_name not in self.schemas:
            raise SchemaValidationError(
                f"Table '{table_name}' not found in schema registry. Registered tables: {list(self.schemas.keys())}"
            )
        return True

    def validate_columns(self, table_name: str, columns: List[str]) -> bool:
        """Check if columns exist in table schema."""
        self.validate_table(table_name)
        registered_columns = self.schemas[table_name]["columns"]
        invalid_columns = [col for col in columns if col not in registered_columns]

        if invalid_columns:
            raise SchemaValidationError(
                f"Columns {invalid_columns} not found in table '{table_name}'. "
                f"Valid columns: {registered_columns}"
            )
        return True

    def get_table_columns(self, table_name: str) -> List[str]:
        """Get list of columns for a table."""
        self.validate_table(table_name)
        return self.schemas[table_name]["columns"]
