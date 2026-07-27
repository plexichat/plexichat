"""ValkeyClient composition class.

Combines all mixins into a single ValkeyClient class.
"""

from .base import ValkeyClientBase
from .basic import BasicMixin
from .json import JSONMixin
from .hash import HashMixin
from .list import ListMixin
from .set import SetMixin
from .counter import CounterMixin
from .pubsub import PubSubMixin
from .lock import LockMixin
from .admin import AdminMixin


class ValkeyClient(
    BasicMixin,
    JSONMixin,
    HashMixin,
    ListMixin,
    SetMixin,
    CounterMixin,
    PubSubMixin,
    LockMixin,
    AdminMixin,
    ValkeyClientBase,
):
    """Valkey connection manager with connection pooling and graceful degradation.

    Composed from domain-specific mixins for maintainability.
    """
