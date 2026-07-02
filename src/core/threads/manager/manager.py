import utils.config as config
import utils.logger as logger
from src.core.base import BaseManager

from .permissions import ThreadPermissionMixin
from .creation import ThreadCreationMixin
from .membership import ThreadMembershipMixin
from .messaging import ThreadMessagingMixin
from .statemgmt import ThreadStateMixin
from .listing import ThreadListingMixin


class ThreadManager(
    ThreadPermissionMixin,
    ThreadCreationMixin,
    ThreadMembershipMixin,
    ThreadMessagingMixin,
    ThreadStateMixin,
    ThreadListingMixin,
    BaseManager,
):
    def __init__(
        self,
        db,
        auth_module=None,
        messaging_module=None,
        servers_module=None,
        notifications_module=None,
    ):
        super().__init__(db, auth_module)
        self._messaging = messaging_module
        self._servers = servers_module
        self._notifications = notifications_module
        self._encrypt_thread_names = config.get(
            "encryption.encrypt_thread_names", False
        )

        logger.info("Threads module initialized")
