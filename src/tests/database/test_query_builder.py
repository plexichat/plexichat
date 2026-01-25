"""
Comprehensive tests for the type-safe query builder.

Tests cover:
- Query building and SQL generation
- Parameter validation and SQL injection prevention
- CRUD operations (INSERT, SELECT, UPDATE, DELETE)
- Pydantic model validation
- Schema registry validation
- Error handling and edge cases
- Support for both SQLite and PostgreSQL
"""

import pytest
import sqlite3
import tempfile
import os

from src.core.database.builder import (
    QueryBuilder,
    SQLInjectionError,
    SchemaValidationError,
    ValidationModelError,
)
from src.core.database.models import (
    UserInsert,
    UserUpdate,
)


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    
    # Create test tables
    conn.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.execute("""
        CREATE TABLE servers (
            id INTEGER PRIMARY KEY,
            owner_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            icon_url TEXT,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.execute("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            channel_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            edited_at TIMESTAMP
        )
    """)
    
    conn.execute("""
        CREATE TABLE channels (
            id INTEGER PRIMARY KEY,
            server_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            channel_type TEXT NOT NULL,
            position INTEGER DEFAULT 0,
            topic TEXT
        )
    """)
    
    conn.commit()
    
    yield conn
    
    # Cleanup
    conn.close()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def builder(temp_db):
    """Create a QueryBuilder instance."""
    return QueryBuilder(temp_db, db_type="sqlite", enable_schema_validation=False)


@pytest.fixture
def builder_with_schema(temp_db):
    """Create a QueryBuilder with schema validation enabled."""
    builder = QueryBuilder(temp_db, db_type="sqlite", enable_schema_validation=True)
    
    # Register schemas
    builder.register_schemas({
        'users': ['id', 'username', 'email', 'password_hash', 'created_at'],
        'servers': ['id', 'owner_id', 'name', 'icon_url', 'description', 'created_at'],
        'messages': ['id', 'channel_id', 'author_id', 'content', 'created_at', 'edited_at'],
        'channels': ['id', 'server_id', 'name', 'channel_type', 'position', 'topic'],
    })
    
    return builder


class TestInsertQuery:
    """Tests for INSERT query building and execution."""
    
    def test_insert_basic(self, builder, temp_db):
        """Test basic INSERT query execution."""
        result = builder.table("users").insert({
            "username": "alice",
            "email": "alice@example.com",
            "password_hash": "hash123456789012345678901234567890123456789012345"
        }).execute()
        
        assert result == 1  # One row inserted
        
        # Verify data was inserted
        cursor = temp_db.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", ("alice",))
        row = cursor.fetchone()
        assert row is not None
        assert row['username'] == "alice"
        assert row['email'] == "alice@example.com"
    
    def test_insert_with_validation(self, builder, temp_db):
        """Test INSERT with Pydantic model validation."""
        result = builder.table("users").insert({
            "username": "bob",
            "email": "bob@example.com",
            "password_hash": "$argon2id$v=19$m=19456,t=2,p=1$abcdefghijklmno$12345678901234567890"
        }).validate(UserInsert).execute()
        
        assert result == 1
        
        cursor = temp_db.cursor()
        cursor.execute("SELECT username FROM users WHERE username = ?", ("bob",))
        row = cursor.fetchone()
        assert row['username'] == "bob"
    
    def test_insert_validation_failure(self, builder):
        """Test INSERT with validation model failure."""
        with pytest.raises(ValidationModelError):
            builder.table("users").insert({
                "username": "ab",  # Too short (min 3)
                "email": "invalid-email",  # Invalid email
                "password_hash": "short"  # Too short
            }).validate(UserInsert).execute()
    
    def test_insert_empty_data(self, builder):
        """Test INSERT with empty data raises error."""
        with pytest.raises(ValueError):
            builder.table("users").insert({}).execute()
    
    def test_insert_sql_injection_attempt_column(self, builder):
        """Test SQL injection prevention in column names."""
        with pytest.raises(SQLInjectionError):
            builder.table("users").insert({
                "username'; DROP TABLE users; --": "value",
                "email": "test@example.com"
            }).execute()
    
    def test_insert_multiple_rows_sequential(self, builder, temp_db):
        """Test inserting multiple rows sequentially."""
        for i in range(3):
            builder.table("users").insert({
                "username": f"user{i}",
                "email": f"user{i}@example.com",
                "password_hash": "hash123456789012345678901234567890123456789012345"
            }).execute()
        
        cursor = temp_db.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM users")
        assert cursor.fetchone()['count'] == 3


class TestSelectQuery:
    """Tests for SELECT query building and execution."""
    
    @pytest.fixture
    def populated_db(self, temp_db):
        """Populate database with test data."""
        cursor = temp_db.cursor()
        cursor.execute("""
            INSERT INTO users (username, email, password_hash) VALUES
            (?, ?, ?)
        """, ("alice", "alice@example.com", "hash1"))
        cursor.execute("""
            INSERT INTO users (username, email, password_hash) VALUES
            (?, ?, ?)
        """, ("bob", "bob@example.com", "hash2"))
        cursor.execute("""
            INSERT INTO users (username, email, password_hash) VALUES
            (?, ?, ?)
        """, ("charlie", "charlie@example.com", "hash3"))
        temp_db.commit()
        return temp_db
    
    def test_select_all_columns(self, builder, populated_db):
        """Test SELECT * query."""
        results = builder.table("users").select().execute()
        assert len(results) == 3
        assert results[0]['username'] in ['alice', 'bob', 'charlie']
    
    def test_select_specific_columns(self, builder, populated_db):
        """Test SELECT with specific columns."""
        results = builder.table("users").select(['username', 'email']).execute()
        assert len(results) == 3
        assert 'username' in results[0].keys()
        assert 'email' in results[0].keys()
    
    def test_select_with_where_equals(self, builder, populated_db):
        """Test SELECT with WHERE = condition."""
        results = builder.table("users").select().where('username', '=', 'alice').execute()
        assert len(results) == 1
        assert results[0]['username'] == 'alice'
    
    def test_select_with_where_not_equals(self, builder, populated_db):
        """Test SELECT with WHERE <> condition."""
        results = builder.table("users").select().where('username', '<>', 'alice').execute()
        assert len(results) == 2
    
    def test_select_with_multiple_conditions(self, builder, populated_db):
        """Test SELECT with multiple WHERE conditions."""
        results = builder.table("users").select().where(
            'username', '=', 'alice'
        ).where('email', '=', 'alice@example.com').execute()
        assert len(results) == 1
    
    def test_select_with_limit(self, builder, populated_db):
        """Test SELECT with LIMIT."""
        results = builder.table("users").select().limit(2).execute()
        assert len(results) == 2
    
    def test_select_with_limit_and_offset(self, builder, populated_db):
        """Test SELECT with LIMIT and OFFSET."""
        results = builder.table("users").select().limit(2).offset(1).execute()
        assert len(results) == 2
    
    def test_select_with_like(self, builder, populated_db):
        """Test SELECT with LIKE operator."""
        results = builder.table("users").select().where('username', 'LIKE', 'a%').execute()
        assert len(results) == 1
        assert results[0]['username'] == 'alice'
    
    def test_select_with_in_operator(self, builder, populated_db):
        """Test SELECT with IN operator."""
        results = builder.table("users").select().where(
            'username', 'IN', ['alice', 'bob']
        ).execute()
        assert len(results) == 2
    
    def test_select_invalid_column(self, builder):
        """Test SELECT with invalid column name raises error."""
        with pytest.raises(SQLInjectionError):
            builder.table("users").select(['username; DROP TABLE users; --']).execute()
    
    def test_select_empty_result(self, builder, populated_db):
        """Test SELECT with no matching results."""
        results = builder.table("users").select().where('username', '=', 'nonexistent').execute()
        assert len(results) == 0


class TestUpdateQuery:
    """Tests for UPDATE query building and execution."""
    
    @pytest.fixture
    def populated_db(self, temp_db):
        """Populate database with test data."""
        cursor = temp_db.cursor()
        cursor.execute("""
            INSERT INTO users (id, username, email, password_hash) VALUES
            (?, ?, ?, ?)
        """, (1, "alice", "alice@example.com", "hash1"))
        cursor.execute("""
            INSERT INTO users (id, username, email, password_hash) VALUES
            (?, ?, ?, ?)
        """, (2, "bob", "bob@example.com", "hash2"))
        temp_db.commit()
        return temp_db
    
    def test_update_basic(self, builder, populated_db):
        """Test basic UPDATE query."""
        result = builder.table("users").update({
            "email": "newemail@example.com"
        }).where('username', '=', 'alice').execute()
        
        assert result == 1  # One row updated
        
        # Verify update
        results = builder.table("users").select().where('username', '=', 'alice').execute()
        assert results[0]['email'] == "newemail@example.com"
    
    def test_update_multiple_columns(self, builder, populated_db):
        """Test UPDATE multiple columns."""
        result = builder.table("users").update({
            "email": "bob.new@example.com",
            "username": "bobby"
        }).where('id', '=', 2).execute()
        
        assert result == 1
        
        results = builder.table("users").select().where('id', '=', 2).execute()
        assert results[0]['email'] == "bob.new@example.com"
        assert results[0]['username'] == "bobby"
    
    def test_update_no_where_raises_error(self, builder):
        """Test UPDATE without WHERE clause raises error (safety feature)."""
        with pytest.raises(ValueError):
            builder.table("users").update({
                "email": "test@example.com"
            }).execute()
    
    def test_update_empty_data(self, builder):
        """Test UPDATE with empty data raises error."""
        with pytest.raises(ValueError):
            builder.table("users").update({}).execute()
    
    def test_update_no_rows(self, builder, populated_db):
        """Test UPDATE with condition matching no rows."""
        result = builder.table("users").update({
            "email": "test@example.com"
        }).where('username', '=', 'nonexistent').execute()
        
        assert result == 0
    
    def test_update_with_validation(self, builder, populated_db):
        """Test UPDATE with validation model."""
        result = builder.table("users").update({
            "username": "new_alice"
        }).validate(UserUpdate).where('id', '=', 1).execute()
        
        assert result == 1


class TestDeleteQuery:
    """Tests for DELETE query building and execution."""
    
    @pytest.fixture
    def populated_db(self, temp_db):
        """Populate database with test data."""
        cursor = temp_db.cursor()
        for i in range(3):
            cursor.execute("""
                INSERT INTO users (username, email, password_hash) VALUES
                (?, ?, ?)
            """, (f"user{i}", f"user{i}@example.com", f"hash{i}"))
        temp_db.commit()
        return temp_db
    
    def test_delete_basic(self, builder, populated_db):
        """Test basic DELETE query."""
        result = builder.table("users").delete().where('username', '=', 'user0').execute()
        assert result == 1
        
        # Verify deletion
        results = builder.table("users").select().where('username', '=', 'user0').execute()
        assert len(results) == 0
    
    def test_delete_multiple_rows(self, builder, populated_db):
        """Test DELETE multiple rows."""
        result = builder.table("users").delete().where('username', 'LIKE', 'user%').execute()
        assert result == 3
        
        results = builder.table("users").select().execute()
        assert len(results) == 0
    
    def test_delete_no_where_raises_error(self, builder):
        """Test DELETE without WHERE clause raises error (safety feature)."""
        with pytest.raises(ValueError):
            builder.table("users").delete().execute()
    
    def test_delete_no_rows(self, builder, populated_db):
        """Test DELETE with condition matching no rows."""
        result = builder.table("users").delete().where('username', '=', 'nonexistent').execute()
        assert result == 0


class TestSchemaRegistry:
    """Tests for schema registry validation."""
    
    def test_register_table(self, builder_with_schema):
        """Test registering a table schema."""
        assert builder_with_schema.schema_registry.validate_table('users')
    
    def test_validate_nonexistent_table(self, builder_with_schema):
        """Test validating nonexistent table raises error."""
        with pytest.raises(SchemaValidationError):
            builder_with_schema.schema_registry.validate_table('nonexistent_table')
    
    def test_validate_columns_success(self, builder_with_schema):
        """Test validating existing columns."""
        assert builder_with_schema.schema_registry.validate_columns(
            'users', ['username', 'email']
        )
    
    def test_validate_columns_invalid(self, builder_with_schema):
        """Test validating nonexistent columns raises error."""
        with pytest.raises(SchemaValidationError):
            builder_with_schema.schema_registry.validate_columns(
                'users', ['username', 'nonexistent_column']
            )
    
    def test_insert_with_schema_validation(self, builder_with_schema):
        """Test INSERT with schema validation."""
        result = builder_with_schema.table("users").insert({
            "username": "alice",
            "email": "alice@example.com",
            "password_hash": "hash1234567890123456789012345678901234567890123456789"
        }).execute()
        
        assert result == 1
    
    def test_insert_invalid_column_with_schema(self, builder_with_schema):
        """Test INSERT with invalid column and schema validation."""
        with pytest.raises(SchemaValidationError):
            builder_with_schema.table("users").insert({
                "username": "alice",
                "invalid_column": "value"
            }).execute()


class TestFluentAPI:
    """Tests for fluent API method chaining."""
    
    def test_select_chain(self, builder, temp_db):
        """Test chaining SELECT methods."""
        cursor = temp_db.cursor()
        for i in range(5):
            cursor.execute("""
                INSERT INTO users (username, email, password_hash) VALUES
                (?, ?, ?)
            """, (f"user{i}", f"user{i}@example.com", f"hash{i}"))
        temp_db.commit()
        
        results = (builder.table("users")
                   .select(['username'])
                   .where('username', 'LIKE', 'user%')
                   .limit(2)
                   .offset(1)
                   .execute())
        
        assert len(results) == 2
    
    def test_insert_chain(self, builder):
        """Test chaining INSERT methods."""
        result = (builder.table("users")
                  .insert({
                      "username": "test",
                      "email": "test@example.com",
                      "password_hash": "$argon2id$v=19$m=19456,t=2,p=1$abcdefghijklmno$12345678901234567890"
                  })
                  .validate(UserInsert)
                  .execute())
        
        assert result == 1
    
    def test_update_chain(self, builder, temp_db):
        """Test chaining UPDATE methods."""
        cursor = temp_db.cursor()
        cursor.execute("""
            INSERT INTO users (id, username, email, password_hash) VALUES
            (?, ?, ?, ?)
        """, (1, "alice", "alice@example.com", "hash1"))
        temp_db.commit()
        
        result = (builder.table("users")
                  .update({"email": "newemail@example.com"})
                  .where('id', '=', 1)
                  .execute())
        
        assert result == 1


class TestErrorHandling:
    """Tests for error handling and validation."""
    
    def test_invalid_table_name(self, builder):
        """Test invalid table name raises error."""
        with pytest.raises(SQLInjectionError):
            builder.table("users; DROP TABLE users; --").insert({
                "username": "test"
            }).execute()
    
    def test_invalid_operator(self, builder):
        """Test invalid WHERE operator raises error."""
        with pytest.raises(ValueError):
            builder.table("users").select().where(
                'username', 'INVALID_OP', 'value'
            ).execute()
    
    def test_invalid_limit_negative(self, builder):
        """Test negative LIMIT raises error."""
        with pytest.raises(ValueError):
            builder.table("users").select().limit(-1).execute()
    
    def test_invalid_offset_negative(self, builder):
        """Test negative OFFSET raises error."""
        with pytest.raises(ValueError):
            builder.table("users").select().offset(-1).execute()
    
    def test_in_operator_requires_list(self, builder, temp_db):
        """Test IN operator requires list/tuple."""
        cursor = temp_db.cursor()
        cursor.execute("""
            INSERT INTO users (username, email, password_hash) VALUES
            (?, ?, ?)
        """, ("alice", "alice@example.com", "hash1"))
        temp_db.commit()
        
        with pytest.raises(ValueError):
            builder.table("users").select().where(
                'username', 'IN', 'not_a_list'
            ).execute()


class TestParameterization:
    """Tests for SQL parameterization and injection prevention."""
    
    def test_insert_parameterization(self, builder, temp_db):
        """Test that INSERT uses parameters, not string concatenation."""
        # This should work and not cause SQL injection
        builder.table("users").insert({
            "username": "user'; DROP TABLE users; --",
            "email": "test@example.com",
            "password_hash": "hash1234567890123456789012345678901234567890123456789"
        }).execute()
        
        # Table should still exist
        cursor = temp_db.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM users")
        assert cursor.fetchone()['count'] == 1
    
    def test_select_parameterization(self, builder, temp_db):
        """Test that SELECT uses parameters for VALUES."""
        cursor = temp_db.cursor()
        cursor.execute("""
            INSERT INTO users (username, email, password_hash) VALUES
            (?, ?, ?)
        """, ("alice", "alice@example.com", "hash1"))
        temp_db.commit()
        
        # Query with potentially malicious value should work safely
        results = builder.table("users").select().where(
            'username', '=', "alice'; DROP TABLE users; --"
        ).execute()
        
        assert len(results) == 0  # No match, which is correct
    
    def test_update_parameterization(self, builder, temp_db):
        """Test that UPDATE uses parameters."""
        cursor = temp_db.cursor()
        cursor.execute("""
            INSERT INTO users (id, username, email, password_hash) VALUES
            (?, ?, ?, ?)
        """, (1, "alice", "alice@example.com", "hash1"))
        temp_db.commit()
        
        result = builder.table("users").update({
            "email": "test'; DROP TABLE users; --"
        }).where('id', '=', 1).execute()
        
        assert result == 1
        
        # Table should still exist
        cursor.execute("SELECT COUNT(*) as count FROM users")
        assert cursor.fetchone()['count'] == 1


class TestQueryBuilderInitialization:
    """Tests for QueryBuilder initialization."""
    
    def test_init_sqlite(self, temp_db):
        """Test QueryBuilder initialization with SQLite."""
        builder = QueryBuilder(temp_db, db_type="sqlite")
        assert builder.db_type == "sqlite"
        assert builder.schema_registry is None
    
    def test_init_with_schema_validation(self, temp_db):
        """Test QueryBuilder initialization with schema validation."""
        builder = QueryBuilder(temp_db, db_type="sqlite", enable_schema_validation=True)
        assert builder.schema_registry is not None
    
    def test_register_schema_without_validation(self, temp_db):
        """Test registering schema without validation enabled raises error."""
        builder = QueryBuilder(temp_db, db_type="sqlite", enable_schema_validation=False)
        
        with pytest.raises(ValueError):
            builder.register_schema('users', ['id', 'username'])


class TestIntegration:
    """Integration tests for complete workflows."""
    
    def test_full_crud_workflow(self, builder, temp_db):
        """Test complete CREATE, READ, UPDATE, DELETE workflow."""
        # CREATE
        builder.table("users").insert({
            "username": "alice",
            "email": "alice@example.com",
            "password_hash": "hash1234567890123456789012345678901234567890123456789"
        }).execute()
        
        # READ
        results = builder.table("users").select().where('username', '=', 'alice').execute()
        assert len(results) == 1
        user = results[0]
        assert user['email'] == "alice@example.com"
        
        # UPDATE
        builder.table("users").update({
            "email": "alice.new@example.com"
        }).where('username', '=', 'alice').execute()
        
        # VERIFY UPDATE
        results = builder.table("users").select().where('username', '=', 'alice').execute()
        assert results[0]['email'] == "alice.new@example.com"
        
        # DELETE
        builder.table("users").delete().where('username', '=', 'alice').execute()
        
        # VERIFY DELETE
        results = builder.table("users").select().where('username', '=', 'alice').execute()
        assert len(results) == 0
    
    def test_multi_table_workflow(self, builder, temp_db):
        """Test working with multiple tables."""
        # Insert into users
        builder.table("users").insert({
            "username": "alice",
            "email": "alice@example.com",
            "password_hash": "hash1234567890123456789012345678901234567890123456789"
        }).execute()
        
        # Insert into servers
        builder.table("servers").insert({
            "owner_id": 1,
            "name": "My Server",
            "icon_url": "https://example.com/icon.png"
        }).execute()
        
        # Verify both inserts
        users = builder.table("users").select().execute()
        servers = builder.table("servers").select().execute()
        
        assert len(users) == 1
        assert len(servers) == 1
        assert servers[0]['name'] == "My Server"
