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

    def _is_server_member(self, user_id: int, server_id: int) -> bool:
        """Authoritative, fail-closed backing-server membership probe.

        Leaving or being kicked deletes the ``srv_members`` row but does NOT
        prune ``thread_members``. Private-thread visibility must therefore
        re-check the server roster instead of trusting a stale membership
        row, or a former member keeps reading the thread and stays in its
        gateway-event audience. A failed lookup denies (no proof = no grant).
        """
        try:
            row = self._db.fetch_one(
                "SELECT 1 FROM srv_members WHERE server_id = ? AND user_id = ?",
                (server_id, user_id),
            )
        except Exception as e:
            logger.error(
                "Server membership probe failed CLOSED for thread access "
                f"(user={user_id}, server={server_id}): {e}"
            )
            return False
        return row is not None

    def can_view_thread(self, user_id: int, thread_id: int) -> bool:
        thread = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        if not thread:
            return False

        if thread.thread_type == ThreadType.PRIVATE:
            # Thread membership alone is not enough: a user removed from the
            # backing server keeps their thread_members row, so the server
            # roster is re-checked here and a failed probe fails closed.
            return self._is_member(user_id, thread_id) and self._is_server_member(
                user_id, thread.server_id
            )

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
