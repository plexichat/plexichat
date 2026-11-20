"""PresenceManager composition class.

Combines all mixins into a single PresenceManager class used by
the presence module and API handlers.
"""

from .base import PresenceManagerBase
from .status import StatusMixin
from .custom_status import CustomStatusMixin
from .activity import ActivityMixin
from .typing import TypingMixin
from .presence import PresenceMixin
from .queries import OnlineQueryMixin
from .visibility import VisibilityMixin
from ..heartbeat_coalesce import HeartbeatWriteCoalescer
from ..models import Presence

# Heartbeats arrive once per connection, and one user can hold several
# connections (or retry a dropped one). Before coalescing, every frame ran the
# full ``update_last_seen`` path: a throttle lookup, a complete presence
# rehydration and a fresh cache write, even when nothing had changed. A bounded
# one-second per-user window collapses those bursts into a single real write.
_heartbeat_write_coalescer = HeartbeatWriteCoalescer()


class PresenceManager(
    StatusMixin,
    CustomStatusMixin,
    ActivityMixin,
    TypingMixin,
    PresenceMixin,
    OnlineQueryMixin,
    VisibilityMixin,
    PresenceManagerBase,
):
    """Core presence manager handling all presence operations.

    Composed from domain-specific mixins for maintainability.
    """

    def update_last_seen(self, user_id: int) -> Presence:
        """Refresh ``last_seen`` with bounded coalescing of heartbeat bursts.

        The first call in a window runs the existing throttled write path in
        ``PresenceMixin``. Repeated calls for the same user inside that window
        skip the DB/cache writes and serve the warm cache snapshot instead, so
        they still report the actually persisted ``last_seen``. Live Valkey TTL
        refreshing and the 30-second DB throttle remain in the real path.
        """
        claim = _heartbeat_write_coalescer.claim(int(user_id))
        if not claim.write:
            return self.get_presence(user_id, use_cache=True)

        presence = PresenceMixin.update_last_seen(self, user_id)
        _heartbeat_write_coalescer.record(int(user_id), presence.last_seen)
        return presence
