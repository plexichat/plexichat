"""
Cache utilities - Provides specialized collection types for caching.
"""

from collections import OrderedDict
from typing import Any


def _close_connection(value: Any) -> None:
    """Try to close a database connection if the value holds one.

    This prevents ResourceWarning when cached values holding sqlite3.Connection
    objects are garbage collected without being properly closed.
    """
    if value is None:
        return
    # Handle (value, expires) tuples from messaging cache
    if isinstance(value, tuple) and len(value) >= 1:
        value = value[0]
    # Only sqlite connections need explicit cleanup here. Avoid invoking
    # arbitrary user-provided close methods stored in the cache.
    try:
        import sqlite3

        if isinstance(value, sqlite3.Connection):
            value.close()
    except Exception:
        pass


class CappedDict(OrderedDict):
    """
    A dictionary with a maximum size, evicting the oldest items (LRU-ish)
    when the limit is reached.
    """

    def __init__(self, max_size: int = 1000, *args, **kwargs):
        self.max_size = max_size
        super().__init__(*args, **kwargs)

    def __setitem__(self, key: Any, value: Any):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        if len(self) > self.max_size:
            self.popitem(last=False)

    def popitem(self, last: bool = True) -> tuple:
        """Remove and return an item, closing any database connections."""
        key, value = super().popitem(last=last)
        _close_connection(value)
        return key, value

    def update_max_size(self, max_size: int):
        """Update the maximum size and trim if necessary."""
        self.max_size = max_size
        while len(self) > self.max_size:
            self.popitem(last=False)
