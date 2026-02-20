"""
Database module - Provides database connectivity for SQLite and PostgreSQL.

This module follows the zero-friction pattern established by common-utils.
It acts as a facade, delegating to engine-specific, monitoring, and dialect components.
"""

from .manager import Database
from .types import DatabaseLocal, DbConnection, DbCursor
from .compat import _CompatibilityRegex, _PLACEHOLDER_PATTERN
from .metrics import _query_count, _query_time_ms
from .worker import with_db_worker

__all__ = [
    "Database",
    "DatabaseLocal",
    "DbConnection",
    "DbCursor",
    "_CompatibilityRegex",
    "_PLACEHOLDER_PATTERN",
    "_query_count",
    "_query_time_ms",
    "with_db_worker",
]
