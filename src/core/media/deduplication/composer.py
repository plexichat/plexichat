"""
DeduplicationManager - composed from base class and mixins.
"""

from .base import DeduplicationManagerBase
from .blocking import BlockingMixin
from .dedup import DeduplicationMixin
from .hashing import HashOperationsMixin
from .reporting import ReportingMixin


class DeduplicationManager(
    DeduplicationManagerBase,
    HashOperationsMixin,
    BlockingMixin,
    DeduplicationMixin,
    ReportingMixin,
):
    """Manages file deduplication and content reporting.

    Composed from:
    - DeduplicationManagerBase: core initialization and configuration
    - HashOperationsMixin: SHA-256 and perceptual hashing
    - BlockingMixin: hash and user blocking
    - DeduplicationMixin: duplicate detection and reference counting
    - ReportingMixin: content reporting and moderation
    """

    __slots__ = ()
