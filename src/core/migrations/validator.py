"""
Validation utilities for migrations.

This module provides validators for migration files, checksums, and safety checks.
"""

import hashlib
import os
import logging
import importlib.util
import re
from typing import List, Tuple, Optional, Iterable

logger = logging.getLogger(__name__)


def validate_migration_order(
    pending_versions: List[str], applied_versions: List[str]
) -> bool:
    """
    Validate that pending migration versions are sequential.

    Only checks for gaps *within the pending versions themselves* and that
    the first pending version follows the highest applied version. Existing
    gaps in already-applied migrations are logged as warnings but do NOT
    block new migrations from being applied.

    Args:
        pending_versions: List of pending migration versions to apply
        applied_versions: List of already applied migration versions

    Returns:
        True if order is valid

    Raises:
        ValueError: If there are gaps within the pending version sequence
    """
    if not pending_versions:
        return True

    # Extract numeric prefixes from version strings
    def _extract_numeric(v: str) -> Optional[int]:
        match = re.match(r"^(\d+)", v)
        if match:
            return int(match.group(1))
        return None

    applied_nums = sorted(
        n for n in (_extract_numeric(v) for v in applied_versions) if n is not None
    )
    pending_nums = sorted(
        n for n in (_extract_numeric(v) for v in pending_versions) if n is not None
    )

    if not pending_nums:
        return True

    # Log (but do not block) gaps in already-applied migrations
    if applied_nums:
        for i in range(1, len(applied_nums)):
            if applied_nums[i] != applied_nums[i - 1] + 1:
                logger.warning(
                    "Gap in applied migration versions: missing version between "
                    "%03d and %03d. This is informational only and will not "
                    "block new migrations.",
                    applied_nums[i - 1],
                    applied_nums[i],
                )

    # Check that pending versions are sequential with no gaps
    for i in range(1, len(pending_nums)):
        if pending_nums[i] != pending_nums[i - 1] + 1:
            msg = (
                "Gap in pending migration version sequence: missing version between "
                f"{pending_nums[i - 1]:03d} and {pending_nums[i]:03d}"
            )
            logger.error(msg)
            raise ValueError(msg)

    # If there are applied migrations, warn (but don't block) if the first
    # pending version doesn't immediately follow the last applied version.
    if applied_nums:
        highest_applied = max(applied_nums)
        first_pending = pending_nums[0]
        if first_pending != highest_applied + 1:
            logger.warning(
                "First pending migration (%03d) does not immediately follow "
                "highest applied migration (%03d). This may indicate a gap, "
                "but will not block the migration.",
                first_pending,
                highest_applied,
            )

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
        if filename.endswith(".py") and filename != "__init__.py":
            # Extract version from filename (e.g., "001_description.py" -> "001")
            parts = filename.split("_")
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
        content = content.encode("utf-8")
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
    if not os.path.exists(file_path):
        raise FileNotFoundError(file_path)

    spec = importlib.util.spec_from_file_location("migration_module", file_path)
    if not spec or not spec.loader:
        raise ValueError(f"Could not load migration file: {file_path}")

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except FileNotFoundError:
        raise
    except Exception as e:
        raise ValueError(f"Error loading migration file {file_path}: {e}")

    if not hasattr(module, "up"):
        raise ValueError(f"Migration file {file_path} missing required up function")

    if not hasattr(module, "down"):
        raise ValueError(f"Migration file {file_path} missing required down function")

    return True


def validate_checksum(
    file_path: str,
    expected_checksum: str,
    actual_checksum: Optional[str] = None,
) -> bool:
    """
    Validate that file content matches expected checksum.

    Args:
        file_path: Path to file (or identifier for test assertions)
        expected_checksum: Expected SHA256 checksum
        actual_checksum: Actual checksum to compare (optional)

    Returns:
        True if matches

    Raises:
        ValueError: If checksum mismatch
    """
    if actual_checksum is None:
        with open(file_path, "rb") as f:
            content = f.read()
        actual_checksum = calculate_checksum(content)

    if actual_checksum != expected_checksum:
        raise ValueError(
            f"Checksum mismatch for {file_path}. Expected {expected_checksum}, got {actual_checksum}"
        )

    return True


_DANGEROUS_SQL_PATTERNS: Iterable[tuple[re.Pattern[str], str]] = (
    (re.compile(r"\bDROP\s+DATABASE\b", re.IGNORECASE), "DROP DATABASE"),
    (re.compile(r"\bDROP\s+SCHEMA\b", re.IGNORECASE), "DROP SCHEMA"),
    (
        re.compile(r"\bTRUNCATE\s+migrations_history\b", re.IGNORECASE),
        "TRUNCATE migrations_history",
    ),
)

_WARNING_SQL_PATTERNS: Iterable[tuple[re.Pattern[str], str]] = (
    (re.compile(r"\bTRUNCATE\b", re.IGNORECASE), "TRUNCATE"),
    (re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE), "DROP TABLE"),
    (re.compile(r"\bALTER\s+TABLE\b", re.IGNORECASE), "ALTER TABLE"),
)


def validate_sql_safety(sql: str) -> tuple[bool, list[str]]:
    """
    Validate SQL safety for migration content.

    Args:
        sql: Raw SQL content

    Returns:
        (is_safe, warnings)

    Raises:
        ValueError: If dangerous SQL is detected
    """
    normalized_sql = sql.strip()

    for pattern, label in _DANGEROUS_SQL_PATTERNS:
        if pattern.search(normalized_sql):
            raise ValueError(f"Dangerous SQL detected: {label}")

    warnings: list[str] = []
    for pattern, label in _WARNING_SQL_PATTERNS:
        if pattern.search(normalized_sql):
            warnings.append(f"Potentially destructive SQL detected: {label}")

    return True, warnings
