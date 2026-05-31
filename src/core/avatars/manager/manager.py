"""AvatarManager - composes all avatar mixins via MRO."""

from src.core.base import BaseManager

from .setup import AvatarSetupMixin
from .caching import AvatarCachingMixin
from .tables import AvatarTablesMixin
from .processing import AvatarProcessingMixin
from .user import AvatarUserMixin
from .server import AvatarServerMixin
from .defaults import AvatarDefaultsMixin


class AvatarManager(
    AvatarSetupMixin,
    AvatarCachingMixin,
    AvatarTablesMixin,
    AvatarProcessingMixin,
    AvatarUserMixin,
    AvatarServerMixin,
    AvatarDefaultsMixin,
    BaseManager,
):
    """AvatarManager - handles avatar and server icon storage and processing.

    Composed via MRO from domain-specific mixins.
    """

    def __init__(self, db=None):
        """Initialize avatar manager."""
        super().__init__(db)
        self._setup_complete = False
