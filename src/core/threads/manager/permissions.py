from typing import Optional

import utils.logger as logger

from ..models import ThreadType
from ..exceptions import PermissionDeniedError

from .helpers import _get_thread_internal
from .protocol import ThreadProtocol


class ThreadPermissionMixin(ThreadProtocol):
    def _check_permission(
        self,
        user_id: int,
        server_id: int,
        permission: str,
        channel_id: Optional[int] = None,
    ) -> bool:
        # SECURITY: fail CLOSED. Without the servers module we cannot
        # evaluate the permission, so deny instead of granting everything.
        if not self._servers:
            logger.error(
                "Thread permission check failed CLOSED: servers module is "
                "not wired into the thread manager. Denying request."
            )
            return False
        return self._servers.has_permission(user_id, server_id, permission, channel_id)

    def _require_permission(
        self,
        user_id: int,
        server_id: int,
        permission: str,
        channel_id: Optional[int] = None,
    ) -> None:
        if not self._check_permission(user_id, server_id, permission, channel_id):
            raise PermissionDeniedError(f"Missing permission: {permission}", permission)

    def can_view_thread(self, user_id: int, thread_id: int) -> bool:
        thread = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        if not thread:
            return False

        if thread.thread_type == ThreadType.PRIVATE:
            return self._is_member(user_id, thread_id)

        return self._check_permission(
            user_id, thread.server_id, "channels.view", thread.channel_id
        )

    def can_send_in_thread(self, user_id: int, thread_id: int) -> bool:
        thread = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        if not thread:
            return False

        if thread.locked:
            return self.can_manage_thread(user_id, thread_id)

        if not self.can_view_thread(user_id, thread_id):
            return False

        return self._check_permission(
            user_id, thread.server_id, "messages.send", thread.channel_id
        )

    def can_manage_thread(self, user_id: int, thread_id: int) -> bool:
        thread = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        if not thread:
            return False

        if thread.owner_id == user_id:
            return True

        if self._servers:
            return self._check_permission(
                user_id, thread.server_id, "threads.manage", thread.channel_id
            )

        return False
