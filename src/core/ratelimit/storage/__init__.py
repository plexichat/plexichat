"""
Rate limit storage backends.
"""

from .base import RateLimitStorage
from .memory import MemoryStorage
from .redis import RedisStorage
from .database import DatabaseStorage

# Maintain backward compatibility
SQLiteStorage = DatabaseStorage

__all__ = [
    "RateLimitStorage",
    "MemoryStorage",
    "RedisStorage",
    "DatabaseStorage",
    "SQLiteStorage",
]