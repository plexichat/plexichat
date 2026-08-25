"""PlexiStash metadata service.

The service never accepts filesystem paths and never stores file bytes. Binary
content belongs to ``src.core.media``; this layer stores opaque media object
references plus organization and authorization metadata.

This module is the stable import facade: the manager implementation is split
across ``stash_manager_*.py`` mixin modules (each under 1000 lines) with the
shared exception types in ``_shared.py``.
"""

from src.core.base import BaseManager

from ._shared import (  # noqa: F401  (facade re-exports)
    StashNotFoundError,
    StashPermissionError,
    StashStorageError,
    StashValidationError,
)
from .stash_manager_base import StashManagerBase
from .stash_manager_collections import StashManagerCollections
from .stash_manager_items import StashManagerItems


class StashManager(
    StashManagerCollections,
    StashManagerItems,
    StashManagerBase,
    BaseManager,
):
    """Persistence and authorization boundary for PlexiStash metadata."""


__all__ = [
    "StashManager",
    "StashNotFoundError",
    "StashPermissionError",
    "StashStorageError",
    "StashValidationError",
]
