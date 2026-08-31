# Database tests

This directory contains unit and integration tests for the database module.
The tests verify query building, security, caching, and connection pooling
across SQLite and PostgreSQL backends.

## Test files

| File | Description |
|------|-------------|
| `test_query_builder.py` | Type-safe query builder: CRUD, SQL injection prevention, schema validation, and fluent API |
| `test_security.py` | Parameterization, transaction isolation, SQL injection prevention, permission checks, and connection pooling |
| `test_cache_ops_extended.py` | In-memory cache tier, valkey fallback, serialization, and pattern invalidation |
| `test_cache_serialization_matrix.py` | Serialization matrix for cache operations |
| `test_cache_reconstructable_models.py` | Reconstruction of cached model objects |

## What these tests cover

### Query builder (`test_query_builder.py`)

- INSERT, SELECT, UPDATE, DELETE with parameterized queries
- Pydantic model validation on insert and update
- Schema registry validation (table and column existence checks)
- Fluent API method chaining (`table().select().where().limit().execute()`)
- SQL injection prevention in table names, column names, and values
- Safety features: UPDATE and DELETE without WHERE clause raise `ValueError`
- `IN` operator requires list/tuple input; negative LIMIT/OFFSET are rejected

### Security (`test_security.py`)

- **Query parameterization** -- INSERT, SELECT, UPDATE, DELETE all use
  parameterized queries; malicious payloads are stored as literal strings
- **Special characters** -- null bytes, unicode, emoji, and injection patterns
  are handled safely
- **Transaction isolation** -- rollback, commit persistence, nested transactions,
  concurrent reads, and autocommit control
- **SQL injection prevention** -- union-based, boolean-based blind, time-based
  blind, stacked queries, comment, and second-order injection attacks are all
  blocked
- **Connection pooling** -- sequential connections, concurrent connections,
  connection reuse, and thread safety under load
- **Permission checks** -- foreign key, unique, NOT NULL, CHECK, and primary
  key constraint enforcement; cascade delete behavior
- **Additional security** -- path traversal prevention, large query handling,
  information disclosure prevention, timing attack resistance, prepared statement
  caching, and connection state isolation

### Cache operations (`test_cache_ops_extended.py`)

- **In-memory tier** -- `mem_cache_set`, `mem_cache_get`, `mem_cache_delete`,
  TTL expiry, and pattern clearing
- **Two-tier fallback** -- valkey unavailable falls back to memory; valkey
  available serves from valkey on hit, memory on miss
- **Bulk operations** -- `cache_get_many` with valkey mget and mem fallback
- **Pattern invalidation** -- wildcard patterns clear both valkey and memory
  tiers
- **Serialization** -- primitives, enums, dataclasses, duck-typed objects,
  and nested structures round-trip through `ensure_serializable` and
  `reconstruct_object`
- **Cache key generation** -- stable, sorted, dict-aware, and prefix-aware keys

## Running the tests

```bash
# Run all database tests
pytest src/tests/database/ -v

# Run query builder tests only
pytest src/tests/database/test_query_builder.py -v

# Run security tests only
pytest src/tests/database/test_security.py -v

# Run cache tests only
pytest src/tests/database/test_cache_ops_extended.py -v

# Run with coverage
pytest src/tests/database/ --cov=src.core.database -v
```

## Key classes and fixtures

- `TestInsertQuery`, `TestSelectQuery`, `TestUpdateQuery`, `TestDeleteQuery` --
  CRUD query building and execution
- `TestSchemaRegistry` -- schema validation for tables and columns
- `TestFluentAPI` -- method chaining for complex queries
- `TestErrorHandling` -- invalid inputs and edge cases
- `TestParameterization` -- SQL injection prevention via parameterization
- `TestQueryBuilderInitialization` -- builder setup with and without schema
  validation
- `TestIntegration` -- complete CRUD workflows across multiple tables
- `TestQueryParameterization` -- parameterized INSERT/SELECT/UPDATE/DELETE
- `TestTransactionIsolation` -- ACID properties and concurrent access
- `TestSQLInjectionPrevention` -- comprehensive attack vector tests
- `TestConnectionPooling` -- concurrent connections and thread safety
- `TestPermissionChecks` -- constraint enforcement and cascade behavior
- `TestDatabaseSecurity` -- additional security hardening tests

## Related source code

- `src/core/database/builder.py` -- `QueryBuilder` class
- `src/core/database/core.py` -- `Database` class with connection pooling
- `src/core/database/cache/operations.py` -- cache get, set, delete, and
  pattern invalidation
- `src/core/database/cache/base.py` -- in-memory cache tier
- `src/core/database/cache/serialization.py` -- serialization and
  reconstruction utilities
- `src/core/database/models.py` -- Pydantic models for validation
