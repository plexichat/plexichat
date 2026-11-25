from typing import Any, Optional

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

    def _live_server_member_ids(self, user_id: int, server_ids: Any) -> set:
        """Batch the live-roster probe used by private-thread listings.

        ``_is_server_member`` answers one ``srv_members`` point read per call,
        so gating a listing of N private threads with it issues N roster
        queries. This resolves every backing server with a single read. The
        returned set holds only server ids that still have a live
        ``srv_members`` row for ``user_id``; a departed member's server is
        absent (no proof = no grant), and a failed lookup yields an empty set,
        so the fail-closed decision is identical to ``_is_server_member``.
        """
        wanted = {int(sid) for sid in server_ids if sid is not None}
        if not wanted:
            return set()
        try:
            rows = self._db.fetch_all(
                "SELECT server_id FROM srv_members WHERE user_id = ?", (user_id,)
            )
        except Exception as e:
            logger.error(
                "Batched server membership probe failed CLOSED for thread "
                f"listing (user={user_id}): {e}"
            )
            return set()
        # SECURITY: an ``srv_members.server_id`` is authoritative proof of a
        # live membership only while it is a genuine integer id. Coercing a
        # malformed value with ``int()`` (e.g. ``"05"``/``5.9``/``True``) would
        # let a row that does not actually prove membership in ``wanted``'s
        # server vouch for it, widening the private-thread audience beyond the
        # per-row ``_is_server_member`` probe this batch replaced. Fail closed:
        # only exact integer ids from live rows can enter the allow set.
        return {
            sid
            for sid in (row.get("server_id") for row in rows or [])
            if type(sid) is int
        } & wanted

    def _can_view_thread_row(self, user_id: int, thread) -> bool:
        """Visibility decision for a thread row the caller already holds.

        ``get_thread`` and the listing filters fetch the ``thread_threads`` row
        before deciding visibility. Re-deriving that decision through
        ``can_view_thread`` used to re-read the *same* row via
        ``_get_thread_internal`` -- one redundant point read (and name decrypt)
        per thread. This mirrors ``can_view_thread`` exactly, minus the fetch:
        private threads are membership + backing-server gated, public ones need
        ``channels.view`` (which fails closed without the servers module).
        """
        if thread.thread_type == ThreadType.PRIVATE:
            return self._is_member(user_id, thread.id) and self._is_server_member(
                user_id, thread.server_id
            )
        return self._check_permission(
            user_id, thread.server_id, "channels.view", thread.channel_id
        )

    def can_view_thread(self, user_id: int, thread_id: int) -> bool:
        thread = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        if not thread:
            return False
        return self._can_view_thread_row(user_id, thread)

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

        # A server moderator's channel-scoped ``threads.manage`` grant is
        # administration authority over a PRIVATE thread even though the
        # moderator is not in that thread's audience: private-thread audiences
        # are membership-only, so requiring thread membership here made the
        # grant unusable. The audience itself stays protected by
        # ``add_member``/``remove_member``, which require caller membership
        # before touching a private thread's member list.
        if thread.thread_type == ThreadType.PRIVATE and not self._is_member(
            user_id, thread.id
        ):
            return self._check_permission(
                user_id, thread.server_id, "threads.manage", thread.channel_id
            )

        # Managing a thread (archive/lock/rename/delete, membership changes)
        # must never reach a thread the caller cannot even see. Ownership and
        # a server-scoped ``threads.manage`` grant both survive a later channel
        # override that revokes ``channels.view`` (or a server removal), so
        # without this gate a hidden thread stays fully operable through the
        # thread endpoints. Reuse the row in hand -- ``_can_view_thread_row``
        # mirrors ``can_view_thread`` without re-reading it -- and fail closed.
        if not self._can_view_thread_row(user_id, thread):
            return False

        if thread.owner_id == user_id:
            return True

        if self._servers:
            return self._check_permission(
                user_id, thread.server_id, "threads.manage", thread.channel_id
            )

        return False
