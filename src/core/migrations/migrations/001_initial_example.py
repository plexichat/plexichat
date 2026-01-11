"""
Initial migration template example.

This migration demonstrates the structure and best practices for writing database
migrations. It shows how to:
- Execute SQL statements
- Handle both SQLite and PostgreSQL
- Provide rollback capability via down() function

Description:
    This example migration creates a simple application_settings table to demonstrate
    the migration pattern. Replace this with actual schema changes needed for your
    application initialization.
"""


def up(db):
    """
    Apply the migration (forward direction).
    
    Args:
        db: Database instance from plexichat.src.core.database
        
    This function is called when applying the migration. It should perform
    all necessary schema changes, data transformations, etc.
    
    Best practices:
    - Use db.execute() for SQL operations
    - Use parameterized queries to prevent SQL injection
    - Handle both SQLite and PostgreSQL syntax differences
    - Keep transactions lightweight and fast
    - Add comments explaining complex operations
    """
    
    # Example: Create a settings table
    sql = """
    CREATE TABLE IF NOT EXISTS application_settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key VARCHAR(255) NOT NULL UNIQUE,
        value TEXT,
        description TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """
    
    db.execute(sql)
    
    # Example: Create an index
    index_sql = """
    CREATE INDEX IF NOT EXISTS idx_application_settings_key 
    ON application_settings(key)
    """
    
    db.execute(index_sql)
    
    # Example: Insert initial data using parameterized query
    insert_sql = """
    INSERT OR IGNORE INTO application_settings (key, value, description)
    VALUES (?, ?, ?)
    """
    
    db.execute(
        insert_sql,
        ('initialized', 'true', 'Flag indicating if application has been initialized')
    )


def down(db):
    """
    Rollback the migration (reverse direction).
    
    Args:
        db: Database instance
        
    This function is optional but highly recommended. It should reverse all
    changes made by the up() function.
    
    Important:
    - Write down() carefully - test in development first
    - Consider data loss if dropping tables or columns
    - Handle dependencies properly
    - Make it idempotent (safe to run multiple times)
    """
    
    # Example: Drop the table
    sql = "DROP TABLE IF EXISTS application_settings"
    db.execute(sql)
