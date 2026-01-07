"""
Cache utilities - Provides specialized collection types for caching.
"""

from collections import OrderedDict
from typing import Any


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

    def update_max_size(self, max_size: int):
        """Update the maximum size and trim if necessary."""
        self.max_size = max_size
        while len(self) > self.max_size:
            self.popitem(last=False)
