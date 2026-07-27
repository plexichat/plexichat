"""
Rate limit storage backends.
"""

from .base import RateLimitStorage
from .memory import MemoryStorage
from .valkey import ValkeyStorage
from .database import DatabaseStorage

# Maintain backward compatibility
SQLiteStorage = DatabaseStorage

__all__ = [
    "RateLimitStorage",
    "MemoryStorage",
    "ValkeyStorage",
    "DatabaseStorage",
    "SQLiteStorage",
]
