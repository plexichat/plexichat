"""
Validation utilities for migrations.

This module provides validators for migration files, checksums, and safety checks.
"""

import hashlib
import os
from pathlib import Path
from typing import List, Tuple


def calculate_checksum(migration_content: bytes) -> str:
    """
    Calculate SHA256 checksum of migration file content.
    
    Args:
        migration_content: Raw bytes of migration file
        
    Returns:
        Hex digest of SHA256 hash
    """
    return hashlib.sha256(migration_content).hexdigest()


def validate_migration_file(migration_path: str) -> bool:
    """
    Validate that a migration file exists and has required structure.
    
    Args:
        migration_path: Path to migration file
        
    Returns:
        True if valid
        
    Raises:
        FileNotFoundError: If migration file doesn't exist
        ValueError: If migration file is missing required functions
    """
    if not os.path.exists(migration_path):
        raise FileNotFoundError(f"Migration file not found: {migration_path}")
    
    with open(migration_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if 'def up(db)' not in content:
        raise ValueError(f"Migration file missing required 'up(db)' function: {migration_path}")
    
    # down() is optional but should be documented
    return True


def validate_checksum(version: str, expected_checksum: str, actual_checksum: str):
    """
    Validate that checksums match.
    
    Args:
        version: Migration version for error reporting
        expected_checksum: Expected SHA256 hash
        actual_checksum: Calculated SHA256 hash
        
    Raises:
        ValueError: If checksums don't match
    """
    if expected_checksum != actual_checksum:
        raise ValueError(
            f"Checksum mismatch for migration {version}: "
            f"expected {expected_checksum}, got {actual_checksum}. "
            f"Migration file may have been tampered with."
        )


def validate_migration_order(pending_versions: List[str], 
                            applied_versions: List[str]) -> bool:
    """
    Validate that migration versions have no gaps.
    
    Args:
        pending_versions: List of pending migration versions to apply
        applied_versions: List of already applied migration versions
        
    Returns:
        True if order is valid
        
    Raises:
        ValueError: If there are gaps in version sequence
    """
    all_versions = sorted(applied_versions + pending_versions)
    
    # Check for gaps in numbering
    for i in range(1, len(all_versions)):
        prev_num = int(all_versions[i-1].lstrip('0') or '0')
        curr_num = int(all_versions[i].lstrip('0') or '0')
        
        if curr_num != prev_num + 1:
            raise ValueError(
                f"Gap in migration version sequence: "
                f"missing version between {all_versions[i-1]} and {all_versions[i]}"
            )
    
    return True


def validate_sql_safety(sql: str) -> Tuple[bool, List[str]]:
    """
    Perform basic safety checks on SQL statements.
    
    Args:
        sql: SQL statement to validate
        
    Returns:
        Tuple of (is_safe, list_of_warnings)
        
    Raises:
        ValueError: If dangerous patterns are detected
    """
    dangerous_patterns = [
        'DROP DATABASE',
        'DROP SCHEMA',
        'DELETE FROM migrations_history',
        'TRUNCATE migrations_history',
    ]
    
    sql_upper = sql.upper()
    warnings = []
    
    for pattern in dangerous_patterns:
        if pattern in sql_upper:
            raise ValueError(
                f"Dangerous SQL pattern detected: '{pattern}'. "
                f"Migration cannot be executed for safety reasons."
            )
    
    # Warnings for potentially problematic patterns
    if 'TRUNCATE' in sql_upper and 'migrations_history' not in sql_upper:
        warnings.append("TRUNCATE operation detected - ensure this is intentional")
    
    if 'DROP TABLE' in sql_upper:
        warnings.append("DROP TABLE operation detected - ensure data backup exists")
    
    if 'ALTER TABLE' in sql_upper:
        warnings.append("ALTER TABLE operation detected - test in staging first")
    
    return True, warnings


def get_migration_files(migrations_dir: str) -> List[Tuple[str, str]]:
    """
    Scan migrations directory and return sorted migration files.
    
    Args:
        migrations_dir: Path to migrations directory
        
    Returns:
        List of tuples (version, file_path) sorted by version
        
    Raises:
        NotADirectoryError: If migrations directory doesn't exist
    """
    if not os.path.isdir(migrations_dir):
        raise NotADirectoryError(f"Migrations directory not found: {migrations_dir}")
    
    migrations = []
    
    for filename in os.listdir(migrations_dir):
        if filename.endswith('.py') and filename != '__init__.py':
            # Extract version from filename (e.g., "001_description.py" -> "001")
            version = filename.split('_')[0]
            file_path = os.path.join(migrations_dir, filename)
            migrations.append((version, file_path))
    
    # Sort by version number
    migrations.sort(key=lambda x: int(x[0]))
    
    return migrations
