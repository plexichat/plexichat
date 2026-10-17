"""
Media deduplication package.

Provides hash-based file deduplication and content reporting:
- SHA-256 hashing of uploaded files
- Perceptual hashing (pHash) for image similarity detection
- Deduplication to avoid storing duplicate files
- Content reporting/blocklist system
- Reference counting for cleanup

For backward compatibility, this package re-exports all public classes,
constants, and functions from the original deduplication module.
"""

from .blocking import BlockingMixin
from .composer import DeduplicationManager
from .constants import (
    DeduplicationResult,
    FileHash,
    HashAlgorithm,
    HashReport,
    ReportStatus,
    SCHEMA,
    create_tables,
    setup,
)
from .dedup import DeduplicationMixin
from .hashing import HashOperationsMixin
from .reporting import ReportingMixin

__all__ = [
    "HashAlgorithm",
    "ReportStatus",
    "FileHash",
    "HashReport",
    "DeduplicationResult",
    "SCHEMA",
    "setup",
    "create_tables",
    "DeduplicationManager",
    "DeduplicationManagerBase",
    "HashOperationsMixin",
    "DeduplicationMixin",
    "BlockingMixin",
    "ReportingMixin",
]

from .base import DeduplicationManagerBase
