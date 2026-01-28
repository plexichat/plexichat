"""
Type-Safe Query Builder Module

Provides a fluent API for building SQL queries with:
- Type safety through Pydantic validation
- SQL injection prevention via parameterization
- Support for SQLite and PostgreSQL
- Clear error messages and IDE autocomplete
- Optional validation before execution

Example Usage:
    # Define validation model
    class UserInsert(BaseModel):
        username: str = Field(min_length=3, max_length=32)
        email: EmailStr
        password_hash: str

    # Use query builder
    builder = QueryBuilder(db)
    result = builder.table("auth_users").insert({
        "username": "alice",
        "email": "alice@example.com",
        "password_hash": hash_password("secret")
    }).validate(UserInsert).execute()
"""

import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Type, TypeVar
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

import utils.logger as logger
from utils.logger import sanitize_data

T = TypeVar('T', bound=BaseModel)


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


class Query(ABC):
    """Base class for all query builders."""
    
    def __init__(self, connection: Any, db_type: str = "sqlite"):
        """Initialize query builder.
        
        Args:
            connection: Database connection object (sqlite3.Connection or psycopg2 connection)
            db_type: Database type ("sqlite" or "postgres")
        """
        self.connection = connection
        self.db_type = db_type
        self._validation_model: Optional[Type[BaseModel]] = None
        self._sql = ""
        self._params: List[Parameter] = []
    
    @abstractmethod
    def build(self) -> tuple[str, list]:
        """Build the SQL query and return (sql, params).
        
        Returns:
            Tuple of (sql_string, list_of_parameters)
        """
        pass
    
    def _validate_identifier(self, identifier: str, identifier_type: str = "column") -> str:
        """Validate and sanitize an identifier (table or column name).
        
        Args:
            identifier: The identifier to validate
            identifier_type: Type of identifier ("table" or "column")
            
        Returns:
            Validated identifier
            
        Raises:
            SQLInjectionError: If identifier contains invalid characters
        """
        # Allow alphanumeric, underscore, and dots (for schema.table)
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)?$', identifier):
            raise SQLInjectionError(
                f"Invalid {identifier_type} name: {identifier}. "
                f"Must start with letter/underscore and contain only alphanumeric/underscore characters."
            )
        return identifier
    
    def _validate_identifiers_list(self, identifiers: List[str], identifier_type: str = "column") -> List[str]:
        """Validate multiple identifiers."""
        return [self._validate_identifier(ident, identifier_type) for ident in identifiers]
    
    def validate(self, model: Type[T]) -> 'Query':
        """Set validation model for data validation before execution.
        
        Args:
            model: Pydantic BaseModel class to validate data against
            
        Returns:
            Self for method chaining
        """
        if not issubclass(model, BaseModel):
            raise ValueError(f"Validation model must be a Pydantic BaseModel, got {type(model)}")
        self._validation_model = model
        return self
    
    def execute(self) -> Any:
        """Execute the query and return results.
        
        Returns:
            Query-specific results (rows for SELECT, row count for INSERT/UPDATE/DELETE)
        """
        sql, params = self.build()
        
        # Sanitize params for logging
        sanitized_params = [
            sanitize_data(p.value if isinstance(p, Parameter) else p) 
            for p in params
        ]
        logger.debug(f"Executing query: {sql} with params: {sanitized_params}")
        
        try:
            if self.db_type == "sqlite":
                cursor = self.connection.cursor()
                # Convert Parameter objects to raw values for SQLite
                param_values = [p.value if isinstance(p, Parameter) else p for p in params]
                cursor.execute(sql, param_values)
                
                # Get results based on query type
                if isinstance(self, SelectQuery):
                    results = cursor.fetchall()
                    logger.debug(f"Query returned {len(results)} rows")
                    return results
                else:
                    # For INSERT, UPDATE, DELETE
                    self.connection.commit()
                    row_count = cursor.rowcount
                    logger.debug(f"Query affected {row_count} rows")
                    cursor.close()
                    return row_count
            else:  # postgres
                cursor = self.connection.cursor()
                # Convert Parameter objects and ? to %s for PostgreSQL
                param_values = [p.value if isinstance(p, Parameter) else p for p in params]
                # Replace ? with %s for PostgreSQL using the dialect utility
                from . import dialect
                pg_sql = dialect.convert_placeholders(sql, self.db_type)
                cursor.execute(pg_sql, param_values)
                
                # Get results based on query type
                if isinstance(self, SelectQuery):
                    results = cursor.fetchall()
                    logger.debug(f"Query returned {len(results)} rows")
                    cursor.close()
                    return results
                else:
                    # For INSERT, UPDATE, DELETE
                    row_count = cursor.rowcount
                    self.connection.commit()
                    logger.debug(f"Query affected {row_count} rows")
                    cursor.close()
                    return row_count
        except Exception as e:
            logger.error(f"Query execution failed: {str(e)}")
            try:
                self.connection.rollback()
            except Exception:
                pass
            raise


class TableQuery(Query):
    """Intermediate query class that represents a table selection."""
    
    def __init__(self, connection: Any, table_name: str, db_type: str = "sqlite", schema_registry: Optional['SchemaRegistry'] = None):
        """Initialize table query.
        
        Args:
            connection: Database connection
            table_name: Name of the table
            db_type: Database type
            schema_registry: Optional schema registry for validation
        """
        super().__init__(connection, db_type)
        self.table_name = self._validate_identifier(table_name, "table")
        self.schema_registry = schema_registry
    
    def insert(self, data: Dict[str, Any]) -> 'InsertQuery':
        """Start building an INSERT query.
        
        Args:
            data: Dictionary of {column: value} pairs
            
        Returns:
            InsertQuery instance for method chaining
        """
        if not isinstance(data, dict):
            raise ValueError(f"Data must be a dictionary, got {type(data)}")
        if not data:
            raise ValueError("Insert data cannot be empty")
        
        return InsertQuery(self.connection, self.table_name, data, self.db_type, self.schema_registry)
    
    def select(self, columns: Optional[List[str]] = None) -> 'SelectQuery':
        """Start building a SELECT query.
        
        Args:
            columns: List of column names to select (None for all columns)
            
        Returns:
            SelectQuery instance for method chaining
        """
        return SelectQuery(self.connection, self.table_name, columns, self.db_type, self.schema_registry)
    
    def update(self, data: Dict[str, Any]) -> 'UpdateQuery':
        """Start building an UPDATE query.
        
        Args:
            data: Dictionary of {column: value} pairs to update
            
        Returns:
            UpdateQuery instance for method chaining
        """
        if not isinstance(data, dict):
            raise ValueError(f"Data must be a dictionary, got {type(data)}")
        if not data:
            raise ValueError("Update data cannot be empty")
        
        return UpdateQuery(self.connection, self.table_name, data, self.db_type, self.schema_registry)
    
    def delete(self) -> 'DeleteQuery':
        """Start building a DELETE query.
        
        Returns:
            DeleteQuery instance for method chaining
        """
        return DeleteQuery(self.connection, self.table_name, self.db_type, self.schema_registry)
    
    def build(self) -> tuple[str, list]:
        """Not implemented for TableQuery."""
        raise NotImplementedError("TableQuery cannot be executed directly. Use insert(), select(), update(), or delete().")


class InsertQuery(TableQuery):
    """Query builder for INSERT statements."""
    
    def __init__(self, connection: Any, table_name: str, data: Dict[str, Any], db_type: str = "sqlite", schema_registry: Optional['SchemaRegistry'] = None):
        """Initialize insert query.
        
        Args:
            connection: Database connection
            table_name: Name of the table
            data: Dictionary of {column: value} pairs
            db_type: Database type
            schema_registry: Optional schema registry for validation
        """
        super().__init__(connection, table_name, db_type, schema_registry)
        self.data = data
    
    def build(self) -> tuple[str, list]:
        """Build the INSERT query."""
        # Validate columns if schema registry exists
        columns = list(self.data.keys())
        validated_columns = self._validate_identifiers_list(columns, "column")
        
        if self.schema_registry:
            self.schema_registry.validate_columns(self.table_name, validated_columns)
        
        # Build SQL with placeholders
        placeholders = ', '.join(['?' for _ in columns])
        columns_str = ', '.join(validated_columns)
        sql = f"INSERT INTO {self.table_name} ({columns_str}) VALUES ({placeholders})"
        
        # Create parameters in same order as columns
        params = [Parameter(self.data[col]) for col in columns]
        
        return sql, params
    
    def validate(self, model: Type[T]) -> 'InsertQuery':
        """Set validation model and validate data.
        
        Args:
            model: Pydantic BaseModel class
            
        Returns:
            Self for method chaining
            
        Raises:
            ValidationModelError: If data fails validation
        """
        super().validate(model)
        
        # Validate data immediately
        try:
            model(**self.data)
            logger.debug(f"Data validated successfully against {model.__name__}")
        except ValidationError as e:
            raise ValidationModelError(f"Validation failed: {str(e)}")
        
        return self


class SelectQuery(TableQuery):
    """Query builder for SELECT statements."""
    
    def __init__(self, connection: Any, table_name: str, columns: Optional[List[str]] = None, db_type: str = "sqlite", schema_registry: Optional['SchemaRegistry'] = None):
        """Initialize select query.
        
        Args:
            connection: Database connection
            table_name: Name of the table
            columns: List of column names (None for all columns)
            db_type: Database type
            schema_registry: Optional schema registry for validation
        """
        super().__init__(connection, table_name, db_type, schema_registry)
        self.columns = columns
        self._where_conditions: List[tuple[str, Any]] = []
        self._limit_value: Optional[int] = None
        self._offset_value: Optional[int] = None
    
    def where(self, column: str, operator: str, value: Any) -> 'SelectQuery':
        """Add a WHERE condition.
        
        Args:
            column: Column name to filter on
            operator: Comparison operator (=, <>, <, >, <=, >=, LIKE, IN, IS NULL, IS NOT NULL)
            value: Value to compare against
            
        Returns:
            Self for method chaining
        """
        column = self._validate_identifier(column, "column")
        
        # Validate operator
        valid_operators = {'=', '<>', '<', '>', '<=', '>=', 'LIKE', 'IN', 'IS NULL', 'IS NOT NULL'}
        if operator.upper() not in valid_operators:
            raise ValueError(f"Invalid operator: {operator}. Must be one of {valid_operators}")
        
        self._where_conditions.append((column, operator, value))
        return self
    
    def limit(self, count: int) -> 'SelectQuery':
        """Set LIMIT clause.
        
        Args:
            count: Maximum number of rows to return
            
        Returns:
            Self for method chaining
        """
        if not isinstance(count, int) or count < 0:
            raise ValueError(f"Limit must be non-negative integer, got {count}")
        self._limit_value = count
        return self
    
    def offset(self, count: int) -> 'SelectQuery':
        """Set OFFSET clause.
        
        Args:
            count: Number of rows to skip
            
        Returns:
            Self for method chaining
        """
        if not isinstance(count, int) or count < 0:
            raise ValueError(f"Offset must be non-negative integer, got {count}")
        self._offset_value = count
        return self
    
    def build(self) -> tuple[str, list]:
        """Build the SELECT query."""
        # Build column list
        if self.columns:
            validated_columns = self._validate_identifiers_list(self.columns, "column")
            columns_str = ', '.join(validated_columns)
            # Validate columns against schema if registry is set
            if self.schema_registry:
                self.schema_registry.validate_columns(self.table_name, validated_columns)
        else:
            columns_str = '*'
        
        # Start building SQL
        sql = f"SELECT {columns_str} FROM {self.table_name}"
        params: List[Parameter] = []
        
        # Add WHERE conditions
        if self._where_conditions:
            where_parts = []
            where_columns = []
            for column, operator, value in self._where_conditions:
                where_columns.append(column)
                if operator in ('IS NULL', 'IS NOT NULL'):
                    where_parts.append(f"{column} {operator}")
                elif operator == 'IN':
                    # Handle IN operator with multiple values
                    if not isinstance(value, (list, tuple)):
                        raise ValueError(f"IN operator requires list/tuple, got {type(value)}")
                    placeholders = ', '.join(['?' for _ in value])
                    where_parts.append(f"{column} IN ({placeholders})")
                    params.extend([Parameter(v) for v in value])
                else:
                    where_parts.append(f"{column} {operator} ?")
                    params.append(Parameter(value))
            
            # Validate WHERE columns against schema if registry is set
            if self.schema_registry:
                self.schema_registry.validate_columns(self.table_name, where_columns)
            
            sql += " WHERE " + " AND ".join(where_parts)
        
        # Add LIMIT and OFFSET
        if self._limit_value is not None:
            sql += f" LIMIT {self._limit_value}"
        if self._offset_value is not None:
            sql += f" OFFSET {self._offset_value}"
        
        return sql, params


class UpdateQuery(TableQuery):
    """Query builder for UPDATE statements."""
    
    def __init__(self, connection: Any, table_name: str, data: Dict[str, Any], db_type: str = "sqlite", schema_registry: Optional['SchemaRegistry'] = None):
        """Initialize update query.
        
        Args:
            connection: Database connection
            table_name: Name of the table
            data: Dictionary of {column: value} pairs to update
            db_type: Database type
            schema_registry: Optional schema registry for validation
        """
        super().__init__(connection, table_name, db_type, schema_registry)
        self.data = data
        self._where_conditions: List[tuple[str, Any]] = []
    
    def validate(self, model: Type[T]) -> 'UpdateQuery':
        """Set validation model and validate data.
        
        Args:
            model: Pydantic BaseModel class to validate data against
            
        Returns:
            Self for method chaining
            
        Raises:
            ValidationModelError: If data fails validation
        """
        super().validate(model)
        
        # Validate data immediately by instantiating the model
        try:
            model(**self.data)
            logger.debug(f"Data validated successfully against {model.__name__}")
        except ValidationError as e:
            raise ValidationModelError(f"Validation failed: {str(e)}")
        
        return self
    
    def where(self, column: str, operator: str, value: Any) -> 'UpdateQuery':
        """Add a WHERE condition.
        
        Args:
            column: Column name to filter on
            operator: Comparison operator
            value: Value to compare against
            
        Returns:
            Self for method chaining
        """
        column = self._validate_identifier(column, "column")
        
        # Validate operator
        valid_operators = {'=', '<>', '<', '>', '<=', '>=', 'LIKE'}
        if operator.upper() not in valid_operators:
            raise ValueError(f"Invalid operator: {operator}. Must be one of {valid_operators}")
        
        self._where_conditions.append((column, operator, value))
        return self
    
    def build(self) -> tuple[str, list]:
        """Build the UPDATE query."""
        if not self._where_conditions:
            raise ValueError("UPDATE requires at least one WHERE condition (safety feature)")
        
        # Validate columns if schema registry exists
        columns = list(self.data.keys())
        validated_columns = self._validate_identifiers_list(columns, "column")
        
        if self.schema_registry:
            self.schema_registry.validate_columns(self.table_name, validated_columns)
        
        # Build SET clause
        set_parts = [f"{col} = ?" for col in validated_columns]
        sql = f"UPDATE {self.table_name} SET {', '.join(set_parts)}"
        
        # Add parameters from data
        params = [Parameter(self.data[col]) for col in columns]
        
        # Add WHERE conditions
        where_parts = []
        for column, operator, value in self._where_conditions:
            where_parts.append(f"{column} {operator} ?")
            params.append(Parameter(value))
        
        sql += " WHERE " + " AND ".join(where_parts)
        
        return sql, params


class DeleteQuery(TableQuery):
    """Query builder for DELETE statements."""
    
    def __init__(self, connection: Any, table_name: str, db_type: str = "sqlite", schema_registry: Optional['SchemaRegistry'] = None):
        """Initialize delete query.
        
        Args:
            connection: Database connection
            table_name: Name of the table
            db_type: Database type
            schema_registry: Optional schema registry for validation
        """
        super().__init__(connection, table_name, db_type, schema_registry)
        self._where_conditions: List[tuple[str, Any]] = []
    
    def where(self, column: str, operator: str, value: Any) -> 'DeleteQuery':
        """Add a WHERE condition.
        
        Args:
            column: Column name to filter on
            operator: Comparison operator
            value: Value to compare against
            
        Returns:
            Self for method chaining
        """
        column = self._validate_identifier(column, "column")
        
        # Validate operator
        valid_operators = {'=', '<>', '<', '>', '<=', '>=', 'LIKE'}
        if operator.upper() not in valid_operators:
            raise ValueError(f"Invalid operator: {operator}. Must be one of {valid_operators}")
        
        self._where_conditions.append((column, operator, value))
        return self
    
    def build(self) -> tuple[str, list]:
        """Build the DELETE query."""
        if not self._where_conditions:
            raise ValueError("DELETE requires at least one WHERE condition (safety feature)")
        
        sql = f"DELETE FROM {self.table_name}"
        params: List[Parameter] = []
        
        # Add WHERE conditions
        where_parts = []
        where_columns = [col for col, _, _ in self._where_conditions]
        
        # Validate WHERE columns against schema if registry is set
        if self.schema_registry:
            self.schema_registry.validate_columns(self.table_name, where_columns)
        
        for column, operator, value in self._where_conditions:
            where_parts.append(f"{column} {operator} ?")
            params.append(Parameter(value))
        
        sql += " WHERE " + " AND ".join(where_parts)
        
        return sql, params


class SchemaRegistry:
    """Registry for table schemas and metadata validation."""
    
    def __init__(self):
        """Initialize schema registry."""
        self.schemas: Dict[str, Dict[str, Any]] = {}
        logger.debug("SchemaRegistry initialized")
    
    def register_table(self, table_name: str, columns: List[str], metadata: Optional[Dict[str, Any]] = None) -> None:
        """Register a table schema.
        
        Args:
            table_name: Name of the table
            columns: List of column names
            metadata: Optional metadata dictionary
        """
        self.schemas[table_name] = {
            'columns': columns,
            'metadata': metadata or {}
        }
        logger.debug(f"Registered schema for table '{table_name}' with columns: {columns}")
    
    def register_tables(self, schemas: Dict[str, List[str]]) -> None:
        """Register multiple table schemas.
        
        Args:
            schemas: Dictionary of {table_name: [columns]}
        """
        for table_name, columns in schemas.items():
            self.register_table(table_name, columns)
    
    def validate_table(self, table_name: str) -> bool:
        """Check if table is registered.
        
        Args:
            table_name: Name of the table
            
        Returns:
            True if table exists in schema
            
        Raises:
            SchemaValidationError: If table not found
        """
        if table_name not in self.schemas:
            raise SchemaValidationError(f"Table '{table_name}' not found in schema registry. Registered tables: {list(self.schemas.keys())}")
        return True
    
    def validate_columns(self, table_name: str, columns: List[str]) -> bool:
        """Check if columns exist in table schema.
        
        Args:
            table_name: Name of the table
            columns: List of column names to validate
            
        Returns:
            True if all columns valid
            
        Raises:
            SchemaValidationError: If columns not found
        """
        self.validate_table(table_name)
        
        registered_columns = self.schemas[table_name]['columns']
        invalid_columns = [col for col in columns if col not in registered_columns]
        
        if invalid_columns:
            raise SchemaValidationError(
                f"Columns {invalid_columns} not found in table '{table_name}'. "
                f"Valid columns: {registered_columns}"
            )
        return True
    
    def get_table_columns(self, table_name: str) -> List[str]:
        """Get list of columns for a table.
        
        Args:
            table_name: Name of the table
            
        Returns:
            List of column names
        """
        self.validate_table(table_name)
        return self.schemas[table_name]['columns']


class QueryBuilder:
    """Main query builder class - entry point for building queries."""
    
    def __init__(self, connection: Any, db_type: str = "sqlite", enable_schema_validation: bool = False):
        """Initialize query builder.
        
        Args:
            connection: Database connection object
            db_type: Database type ("sqlite" or "postgres")
            enable_schema_validation: Enable schema registry validation
        """
        self.connection = connection
        self.db_type = db_type
        self.schema_registry = SchemaRegistry() if enable_schema_validation else None
        logger.debug(f"QueryBuilder initialized with db_type={db_type}, schema_validation={enable_schema_validation}")
    
    def table(self, table_name: str) -> TableQuery:
        """Start building a query for a table.
        
        Args:
            table_name: Name of the table
            
        Returns:
            TableQuery instance for method chaining
        """
        return TableQuery(self.connection, table_name, self.db_type, self.schema_registry)
    
    def register_schema(self, table_name: str, columns: List[str]) -> None:
        """Register a table schema.
        
        Args:
            table_name: Name of the table
            columns: List of column names
        """
        if self.schema_registry is None:
            raise ValueError("Schema registry not enabled. Initialize QueryBuilder with enable_schema_validation=True")
        self.schema_registry.register_table(table_name, columns)
    
    def register_schemas(self, schemas: Dict[str, List[str]]) -> None:
        """Register multiple table schemas.
        
        Args:
            schemas: Dictionary of {table_name: [columns]}
        """
        if self.schema_registry is None:
            raise ValueError("Schema registry not enabled. Initialize QueryBuilder with enable_schema_validation=True")
        self.schema_registry.register_tables(schemas)
