"""
RAM Cache Module - In-memory caching for small, frequently-read reference tables.

This module provides in-memory caching for small tables that:
- Are read frequently
- Change rarely
- Have a small number of rows (< 1000)

Examples: search_categories, voice_afk_settings, notification_settings

Usage:
    from src.core.database.ram_cache import RAMCache
    
    # Create cache for a table
    categories_cache = RAMCache("search_categories", ttl=3600)
    
    # Load data
    categories_cache.load(db, "SELECT * FROM search_categories")
    
    # Get all items
    all_cats = categories_cache.get_all()
    
    # Get by key
    gaming = categories_cache.get("id", "gaming")
    
    # Invalidate on write
    categories_cache.invalidate()
"""

import time
import threading
from typing import Any, Dict, List, Optional, Callable

import utils.logger as logger


class RAMCache:
    """
    Thread-safe in-memory cache for small reference tables.
    """

    def __init__(self, name: str, ttl: int = 300, max_items: int = 1000):
        """
        Initialize RAM cache.
        
        Args:
            name: Cache name (for logging)
            ttl: Time-to-live in seconds (default 5 minutes)
            max_items: Maximum items to cache (safety limit)
        """
        self.name = name
        self.ttl = ttl
        self.max_items = max_items
        self._data: List[Dict[str, Any]] = []
        self._index: Dict[str, Dict[Any, Dict[str, Any]]] = {}  # field -> value -> row
        self._loaded_at: float = 0
        self._lock = threading.RLock()
        self._query: Optional[str] = None
        self._db: Any = None

    def load(self, db, query: str, params: Optional[tuple[Any, ...]] = None) -> int:
        """
        Load data from database into cache.
        
        Args:
            db: Database instance
            query: SQL query to fetch data
            params: Optional query parameters
            
        Returns:
            Number of items loaded
        """
        with self._lock:
            self._db = db
            self._query = query

            try:
                rows = db.fetch_all(query, params) if params is not None else db.fetch_all(query)

                if len(rows) > self.max_items:
                    logger.warning(
                        f"RAMCache '{self.name}': Query returned {len(rows)} rows, "
                        f"exceeds max_items={self.max_items}. Truncating."
                    )
                    rows = rows[:self.max_items]

                # Convert rows to dicts
                self._data = [dict(row) for row in rows]
                self._index = {}
                self._loaded_at = time.time()

                logger.debug(f"RAMCache '{self.name}': Loaded {len(self._data)} items")
                return len(self._data)

            except Exception as e:
                logger.error(f"RAMCache '{self.name}': Failed to load - {e}")
                self._data = []
                self._index = {}
                raise

    def _ensure_index(self, field: str) -> None:
        """Build index for a field if not exists."""
        if field not in self._index:
            self._index[field] = {}
            for row in self._data:
                if field in row:
                    self._index[field][row[field]] = row

    def is_valid(self) -> bool:
        """Check if cache is still valid (not expired)."""
        if not self._data:
            return False
        return (time.time() - self._loaded_at) < self.ttl

    def _maybe_reload(self) -> None:
        """Reload cache if expired and we have db/query."""
        if not self.is_valid() and self._db is not None and self._query is not None:
            try:
                self.load(self._db, self._query)
            except Exception as e:
                logger.error(f"RAMCache '{self.name}': Reload failed - {e}")

    def get_all(self) -> List[Dict[str, Any]]:
        """Get all cached items."""
        with self._lock:
            self._maybe_reload()
            return self._data.copy()

    def get(self, field: str, value: Any) -> Optional[Dict[str, Any]]:
        """
        Get item by field value.
        
        Args:
            field: Field name to search
            value: Value to match
            
        Returns:
            Matching row or None
        """
        with self._lock:
            self._maybe_reload()
            self._ensure_index(field)
            return self._index.get(field, {}).get(value)

    def get_many(self, field: str, values: List[Any]) -> List[Dict[str, Any]]:
        """
        Get multiple items by field values.
        
        Args:
            field: Field name to search
            values: List of values to match
            
        Returns:
            List of matching rows
        """
        with self._lock:
            self._maybe_reload()
            self._ensure_index(field)
            index = self._index.get(field, {})
            return [index[v] for v in values if v in index]

    def filter(self, predicate: Callable[[Dict[str, Any]], bool]) -> List[Dict[str, Any]]:
        """
        Filter items by predicate function.
        
        Args:
            predicate: Function that returns True for items to include
            
        Returns:
            List of matching rows
        """
        with self._lock:
            self._maybe_reload()
            return [row for row in self._data if predicate(row)]

    def invalidate(self) -> None:
        """Invalidate cache (force reload on next access)."""
        with self._lock:
            self._loaded_at = 0
            logger.debug(f"RAMCache '{self.name}': Invalidated")

    def clear(self) -> None:
        """Clear all cached data."""
        with self._lock:
            self._data = []
            self._index = {}
            self._loaded_at = 0
            logger.debug(f"RAMCache '{self.name}': Cleared")

    def stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            return {
                "name": self.name,
                "items": len(self._data),
                "ttl": self.ttl,
                "valid": self.is_valid(),
                "age_seconds": time.time() - self._loaded_at if self._loaded_at else None,
                "indexed_fields": list(self._index.keys()),
            }


# Global caches for common reference tables
_caches: Dict[str, RAMCache] = {}


def get_cache(name: str) -> Optional[RAMCache]:
    """Get a named cache."""
    return _caches.get(name)


def create_cache(name: str, ttl: int = 300, max_items: int = 1000) -> RAMCache:
    """Create or get a named cache."""
    if name not in _caches:
        _caches[name] = RAMCache(name, ttl, max_items)
    return _caches[name]


def invalidate_all() -> None:
    """Invalidate all caches."""
    for cache in _caches.values():
        cache.invalidate()


def clear_all() -> None:
    """Clear all caches."""
    for cache in _caches.values():
        cache.clear()


def all_stats() -> Dict[str, Dict[str, Any]]:
    """Get stats for all caches."""
    return {name: cache.stats() for name, cache in _caches.items()}
