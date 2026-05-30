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
