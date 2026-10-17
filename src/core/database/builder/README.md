# Query builder

The query builder provides a type-safe, fluent API for constructing SQL queries. It supports `SELECT`, `INSERT`, `UPDATE`, and `DELETE` operations with identifier validation, parameterized queries, optional Pydantic model validation, and schema registry enforcement.

The builder is designed to prevent SQL injection by validating all identifiers (table and column names) against a regex pattern and using parameterized placeholders for all values.

## Architecture

```
QueryBuilder (entry point)
└── TableQuery (table selection)
    ├── InsertQuery  — INSERT builder
    ├── SelectQuery  — SELECT builder with WHERE, LIMIT, OFFSET
    ├── UpdateQuery  — UPDATE builder with WHERE
    └── DeleteQuery  — DELETE builder (requires WHERE)
```

The `SchemaRegistry` optionally validates table and column names against a pre-registered schema. The `QueryBuilderProtocol` defines the shared interface for mixin delegation.

## Key files

| File | Purpose |
|------|---------|
| `__init__.py` | Package entry point; exports all public classes |
| `composer.py` | `QueryBuilder` — main entry point for building queries |
| `base.py` | `Query`, `TableQuery`, `Parameter`, exception classes |
| `protocol.py` | `QueryBuilderProtocol` — interface contract for mixins |
| `schema_registry.py` | `SchemaRegistry` — table/column validation |
| `select.py` | `SelectQuery` — SELECT statement builder |
| `insert.py` | `InsertQuery` — INSERT statement builder |
| `update.py` | `UpdateQuery` — UPDATE statement builder |
| `delete.py` | `DeleteQuery` — DELETE statement builder |
| `where.py` | `build_where_clause()` — shared WHERE clause logic |

## Usage

Create a builder with a database connection:

```python
from src.core.database.builder import QueryBuilder

qb = QueryBuilder(connection, db_type="sqlite")
```

### SELECT

```python
query = (
    qb.table("users")
    .select(["id", "username", "email"])
    .where("id", "=", 123)
    .limit(10)
    .offset(0)
)
results = query.execute()
```

### INSERT

```python
last_id = (
    qb.table("users")
    .insert({"username": "alice", "email": "alice@example.com"})
    .execute()
)
```

### UPDATE

```python
rows_updated = (
    qb.table("users")
    .update({"email": "new@example.com"})
    .where("id", "=", 123)
    .execute()
)
```

### DELETE

```python
rows_deleted = (
    qb.table("users")
    .delete()
    .where("id", "=", 123)
    .execute()
)
```

### Pydantic validation

```python
from pydantic import BaseModel

class UserCreate(BaseModel):
    username: str
    email: str

last_id = (
    qb.table("users")
    .insert({"username": "alice", "email": "alice@example.com"})
    .validate(UserCreate)
    .execute()
)
```

### Schema registry

```python
qb = QueryBuilder(connection, db_type="sqlite", enable_schema_validation=True)
qb.register_schemas({
    "users": ["id", "username", "email", "created_at"],
    "sessions": ["id", "user_id", "token", "expires_at"],
})

# This raises SchemaValidationError if "invalid_column" is not registered
qb.table("users").select(["id", "invalid_column"]).execute()
```

## Supported operators

The WHERE clause supports the following operators:

| Operator | Description |
|----------|-------------|
| `=` | Equality |
| `<>` | Inequality |
| `<`, `>`, `<=`, `>=` | Comparison |
| `LIKE` | Pattern matching |
| `IN` | Set membership (requires list/tuple) |
| `IS NULL` | Null check (unary) |
| `IS NOT NULL` | Not-null check (unary) |

## Safety features

- **Identifier validation** — table and column names must match `^[a-zA-Z_][a-zA-Z0-9_]*` (`src/core/database/builder/base.py:99`)
- **Parameterized queries** — all values use `?` placeholders, never string interpolation (`src/core/database/builder/base.py:138`)
- **DELETE/UPDATE WHERE requirement** — both require at least one WHERE condition as a safety guard (`src/core/database/builder/delete.py:29`, `src/core/database/builder/update.py:49`)
- **SQL injection detection** — raises `SQLInjectionError` for invalid identifiers (`src/core/database/builder/base.py:31`)
- **Schema validation** — optionally validates table and column existence (`src/core/database/builder/schema_registry.py:43`)

## Exception hierarchy

| Exception | Description |
|-----------|-------------|
| `QueryBuilderException` | Base exception for all builder errors |
| `SQLInjectionError` | Invalid identifier detected |
| `SchemaValidationError` | Table or column not in registry |
| `ValidationModelError` | Pydantic model validation failed |

## Related code

- `src/core/database/__init__.py` — `Database` facade that exposes `qb` property
- `src/core/database/dialect.py` — `convert_placeholders()` for PostgreSQL `$1` syntax
