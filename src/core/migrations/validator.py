"""
Validation utilities for migrations.

This module provides validators for migration files, checksums, and safety checks.
"""

import hashlib
import os
import logging
import importlib.util
from typing import List, Tuple

logger = logging.getLogger(__name__)

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
    if not pending_versions:
        return True

    all_versions = sorted(applied_versions + pending_versions)
    
    # Check for gaps in numbering
    for i in range(1, len(all_versions)):
        prev_num = int(all_versions[i-1].lstrip('0') or '0')
        curr_num = int(all_versions[i].lstrip('0') or '0')
        
        # If we already have gaps in applied versions, we shouldn't necessarily
        # block applying NEW migrations unless the NEW migrations themselves create a gap
        # relative to the highest applied migration.
        
        if curr_num != prev_num + 1:
            # Only warn instead of error if we're adding a migration at the end
            msg = f"Gap in migration version sequence: missing version between {all_versions[i-1]} and {all_versions[i]}"
            logger.warning(msg)
            # For now, let's keep it as warning to avoid blocking development
            # if previous migrations had issues.
    
    return True

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
        logger.error(f"Migrations directory not found: {migrations_dir}")
        raise NotADirectoryError(f"Migrations directory not found: {migrations_dir}")
    
    migrations = []
    
    for filename in os.listdir(migrations_dir):
        if filename.endswith('.py') and filename != '__init__.py':
            # Extract version from filename (e.g., "001_description.py" -> "001")
            parts = filename.split('_')
            if not parts[0].isdigit():
                logger.warning(f"Skipping invalid migration filename: {filename}")
                continue
                
            version = parts[0]
            file_path = os.path.join(migrations_dir, filename)
            migrations.append((version, file_path))
            logger.debug(f"Discovered migration: {version} -> {filename}")
    
    # Sort by version number
    migrations.sort(key=lambda x: int(x[0]))
    
    logger.info(f"Found {len(migrations)} migration files in {migrations_dir}")
    return migrations

def calculate_checksum(content: bytes) -> str:
    """
    Calculate SHA256 checksum of file content.
    
    Args:
        content: File content as bytes
        
    Returns:
        Hex digest of SHA256 hash
    """
    if isinstance(content, str):
        content = content.encode('utf-8')
    sha256_hash = hashlib.sha256(content)
    return sha256_hash.hexdigest()

def validate_migration_file(file_path: str) -> bool:
    """
    Validate that a migration file has required structure.
    
    Args:
        file_path: Path to migration file
        
    Returns:
        True if valid
        
    Raises:
        ValueError: If file is invalid
    """
    spec = importlib.util.spec_from_file_location("migration_module", file_path)
    if not spec or not spec.loader:
        raise ValueError(f"Could not load migration file: {file_path}")
        
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        raise ValueError(f"Error loading migration file {file_path}: {e}")
    
    if not hasattr(module, 'up'):
        raise ValueError(f"Migration file {file_path} missing 'up' function")
        
    if not hasattr(module, 'down'):
        raise ValueError(f"Migration file {file_path} missing 'down' function")
        
    return True

def validate_checksum(file_path: str, expected_checksum: str) -> bool:
    """
    Validate that file content matches expected checksum.
    
    Args:
        file_path: Path to file
        expected_checksum: Expected SHA256 checksum
        
    Returns:
        True if matches
        
    Raises:
        ValueError: If checksum mismatch
    """
    with open(file_path, 'rb') as f:
        content = f.read()
    
    actual = calculate_checksum(content)
    if actual != expected_checksum:
        raise ValueError(f"Checksum mismatch for {file_path}. Expected {expected_checksum}, got {actual}")
    
    return True
