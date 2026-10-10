"""
Thread-safe bounded LRU cache used by the API app factory.

Extracted from app.py to keep that module focused on application
wiring; import site in app.py is unchanged in behavior.
"""

from collections import OrderedDict
import threading


class LRUCache:
    """Simple thread-safe bounded LRU cache that behaves like a dict for get/setitem/contains."""

    def __init__(self, maxsize: int = 1024):
        self._cache: OrderedDict = OrderedDict()
        self._maxsize = maxsize
        self._lock = threading.Lock()

    def get(self, key, default=None):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            return default

    def __setitem__(self, key, value):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = value
            if len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)

    def __contains__(self, key):
        with self._lock:
            return key in self._cache

    def pop(self, key, default=None):
        with self._lock:
            return self._cache.pop(key, default)
