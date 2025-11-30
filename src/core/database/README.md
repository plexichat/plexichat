# Database Module

A database connectivity module supporting SQLite and PostgreSQL with a clean, consistent API.

## Features

- SQLite and PostgreSQL support
- Unified query syntax - use `?` placeholders for both databases (auto-converted to `%s` for PostgreSQL)
- Parameterized queries for SQL injection prevention
- Fetch helpers (fetch_one, fetch_all)
- Transaction support
- Context manager support
- Automatic directory creation for SQLite
- Dict-like row access for both SQLite and PostgreSQL
- Integrated logging

## Requirements

```bash
pip install PyYAML  # For config (via common-utils)
pip install psycopg2-binary  # Only if using PostgreSQL
```

## Configuration

The database module reads configuration from the `database` key in your config file:

```yaml
database:
  type: sqlite  # or "postgres"
  path: data/app.db  # SQLite only
  
  # PostgreSQL settings (only used when type: postgres)
  postgres:
    host: localhost
    port: 5432
    user: postgres
    password: ""
    dbname: plexichat
```

### Switching to PostgreSQL

1. Install the PostgreSQL driver:
   ```bash
   pip install psycopg2-binary
   ```

2. Update your config:
   ```yaml
   database:
     type: postgres
     postgres:
       host: localhost
       port: 5432
       user: postgres
       password: your_password
       dbname: plexichat
   ```

3. Ensure your PostgreSQL server is running and the database exists.

## Usage

### Basic Usage

```python
from core.database import Database

db = Database()
db.connect()

# Create table
db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)")

# Insert data - use ? placeholders (works for both SQLite and PostgreSQL)
db.execute("INSERT INTO users (name, email) VALUES (?, ?)", ("Alice", "alice@example.com"))

# Fetch single row - returns dict-like object
user = db.fetch_one("SELECT * FROM users WHERE id = ?", (1,))
print(user["name"])  # Alice

# Fetch all rows
users = db.fetch_all("SELECT * FROM users")
for user in users:
    print(user["name"])

db.close()
```

### Cross-Database Compatibility

The module automatically converts `?` placeholders to `%s` for PostgreSQL, so you can write database-agnostic code:

```python
# This works for both SQLite and PostgreSQL
db.execute("INSERT INTO users (name) VALUES (?)", ("Bob",))
db.fetch_one("SELECT * FROM users WHERE name = ?", ("Bob",))
```

### Context Manager

```python
from core.database import Database

with Database() as db:
    db.execute("INSERT INTO users (name) VALUES (?)", ("Bob",))
    users = db.fetch_all("SELECT * FROM users")
# Connection automatically closed
```

### Batch Operations

```python
db.execute_many(
    "INSERT INTO users (name, email) VALUES (?, ?)",
    [
        ("Alice", "alice@example.com"),
        ("Bob", "bob@example.com"),
        ("Charlie", "charlie@example.com"),
    ]
)
```

### Transactions

```python
db.begin_transaction()
try:
    db.execute("INSERT INTO accounts (user_id, balance) VALUES (?, ?)", (1, 100))
    db.execute("UPDATE accounts SET balance = balance - 50 WHERE user_id = ?", (1,))
    db.commit()
except Exception:
    db.rollback()
    raise
```

### Check Table Existence

```python
if not db.table_exists("users"):
    db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
```

## API Reference

### Database Class

#### `__init__()`
Initialize the database manager. Reads configuration from config module.

#### `connect()`
Establish connection to the database.

#### `execute(query, params=None)`
Execute a query with optional parameters. Returns cursor.

#### `execute_many(query, params_list)`
Execute a query multiple times with different parameters.

#### `fetch_one(query, params=None)`
Execute query and return single result or None.

#### `fetch_all(query, params=None)`
Execute query and return all results as list.

#### `table_exists(table_name)`
Check if a table exists. Returns boolean.

#### `begin_transaction()`
Start a transaction.

#### `commit()`
Commit current transaction.

#### `rollback()`
Rollback current transaction.

#### `close()`
Close the database connection.

## Error Handling

The module raises appropriate exceptions:

- `ValueError`: Invalid configuration or unsupported database type
- `ConnectionError`: Operations attempted without connection
- `ImportError`: psycopg2 not installed when using PostgreSQL
- `sqlite3.Error`: SQLite-specific errors
- `psycopg2.Error`: PostgreSQL-specific errors

All errors are logged before being raised.

## Thread Safety

SQLite connections are not thread-safe by default. For multi-threaded applications:
- Create a new Database instance per thread, or
- Use connection pooling (future enhancement)

## PostgreSQL Notes

- Uses `psycopg2-binary` driver (sync, not async)
- Uses `RealDictCursor` for dict-like row access matching SQLite behavior
- Autocommit is disabled by default to match SQLite transaction behavior
- The `?` placeholder syntax is automatically converted to `%s`

## Testing

```bash
pytest src/tests/test_database.py -v
```

PostgreSQL tests are skipped if no PostgreSQL server is available.
