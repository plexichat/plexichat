"""RedisClient composition class.

Combines all mixins into a single RedisClient class.
"""

from .base import RedisClientBase
from .basic import BasicMixin
from .json import JSONMixin
from .hash import HashMixin
from .list import ListMixin
from .set import SetMixin
from .counter import CounterMixin
from .pubsub import PubSubMixin
from .lock import LockMixin
from .admin import AdminMixin


class RedisClient(
    BasicMixin,
    JSONMixin,
    HashMixin,
    ListMixin,
    SetMixin,
    CounterMixin,
    PubSubMixin,
    LockMixin,
    AdminMixin,
    RedisClientBase,
):
    """Redis connection manager with connection pooling and graceful degradation.

    Composed from domain-specific mixins for maintainability.
    """
