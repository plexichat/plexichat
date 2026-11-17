"""Visibility operations mixin.

Handles presence visibility rules including invisible mode,
block relationships, and efficient bulk visibility checks.
"""

from dataclasses import replace
from typing import Dict, List

from .base import PresenceManagerBase
from ..models import Presence, UserStatus


class VisibilityMixin(PresenceManagerBase):
    """Mixin providing presence visibility operations."""

    @staticmethod
    def _scrub_private_fields(presence: Presence) -> Presence:
        """Return ``presence`` with private focus state removed.

        ``current_channel_id`` / ``current_server_id`` are transient
        "focused channel" markers kept in Valkey purely so the notification
        pipeline can suppress a ping when the recipient is already looking at
        the channel (see ``NotificationCreator._should_notify_user_optimized``,
        which reads ``get_presence`` directly). They are NOT part of the public
        presence contract and must never cross the viewer boundary: leaking
        them reveals the exact channel and server another user is currently
        viewing, even to a viewer who is merely a shared-server member (the
        core visibility layer has no friends gate of its own).

        The notification path is unaffected because it never goes through the
        ``get_visible_*`` methods.
        """
        if presence.current_channel_id is None and presence.current_server_id is None:
            return presence
        return replace(
            presence,
            current_channel_id=None,
            current_server_id=None,
        )

    def get_visible_presence(self, viewer_id: int, target_id: int) -> Presence:
        """
        Get presence as visible to a specific viewer.

        Respects invisible mode and block relationships.
        Blocked users see target as offline.
        Invisible users appear offline to others.

        Args:
            viewer_id: ID of the user viewing
            target_id: ID of the user being viewed

        Returns:
            Presence (may show offline if invisible or blocked)
        """
        presence = self.get_presence(target_id)

        if int(viewer_id) == int(target_id):
            return presence

        if self._relationships:
            if self._relationships.is_blocked_by_either(viewer_id, target_id):
                return Presence(
                    user_id=target_id,
                    status=UserStatus.OFFLINE,
                    custom_status=None,
                    activity=None,
                    last_seen=0,
                    updated_at=0,
                )

        # CORRECTNESS FIX: invisible mode must NOT leak custom_status,
        # ``last_seen`` or ``updated_at`` to anyone but the user
        # themselves. The previous implementation forwarded the real
        # custom_status + real timestamps inside an OFFLINE-shaped
        # object, which let any viewer reconstruct the user's
        # availability window. We now return a strictly empty
        # presence that is indistinguishable from "has never been
        # online".
        if presence.status == UserStatus.INVISIBLE:
            return Presence(
                user_id=target_id,
                status=UserStatus.OFFLINE,
                custom_status=None,
                activity=None,
                last_seen=0,
                updated_at=0,
            )

        # Any non-self viewer gets the public projection only. The branch
        # above already returned a freshly-built offline presence for an
        # invisible target (whose focus fields default to None); for a
        # normally-visible target we must strip the focus fields explicitly.
        return self._scrub_private_fields(presence)

    def get_visible_presences_bulk(
        self, viewer_id: int, target_ids: List[int]
    ) -> Dict[int, Presence]:
        """
        Get visible presence for multiple users efficiently.

        Args:
            viewer_id: ID of the user viewing
            target_ids: List of user IDs to view

        Returns:
            Dict mapping user_id to Presence
        """
        presences = self.get_presences(target_ids)
        result: Dict[int, Presence] = {}

        # A single two-way lookup is enough: get_all_blocked_ids() is the
        # UNION of "users the viewer blocked" and "users who blocked the
        # viewer", i.e. exactly the set that must render offline. The old
        # code also fetched get_blocked_user_ids() (a strict subset) only to
        # subtract a "blocked-by" set that was then re-joined with the same
        # membership test — a redundant cache/DB round trip on every bulk
        # presence read (relationship listing, DM hydration, gateway fan-out).
        blocked_ids: set[int] = set()
        if self._relationships:
            try:
                blocked_ids = {
                    int(uid)
                    for uid in self._relationships.get_all_blocked_ids(viewer_id)
                }
            except Exception as visibility_error:
                # Fail closed: without the union, fall back to the viewer's
                # own block list so a failing lookup cannot expose a blocked
                # user's real presence.
                try:
                    blocked_ids = {
                        int(uid)
                        for uid in self._relationships.get_blocked_user_ids(viewer_id)
                    }
                except Exception:
                    pass
                import utils.logger as logger

                logger.debug(
                    f"presence visibility cache store failed: {visibility_error}"
                )

        for p in presences:
            target_id_raw = p.user_id
            target_id = int(target_id_raw)

            if int(viewer_id) == target_id:
                result[target_id_raw] = p
                continue

            is_blocked = target_id in blocked_ids
            if is_blocked:
                result[target_id_raw] = Presence(
                    user_id=target_id_raw,
                    status=UserStatus.OFFLINE,
                    custom_status=None,
                    activity=None,
                    last_seen=0,
                    updated_at=0,
                )
                continue

            # CORRECTNESS FIX: same per-target scrub as
            # :meth:`get_visible_presence` — no leak of custom_status,
            # last_seen or updated_at for an INVISIBLE user.
            if p.status == UserStatus.INVISIBLE:
                result[target_id_raw] = Presence(
                    user_id=target_id_raw,
                    status=UserStatus.OFFLINE,
                    custom_status=None,
                    activity=None,
                    last_seen=0,
                    updated_at=0,
                )
            else:
                # Same privacy rule as the single-target path: a viewer never
                # receives another user's focused-channel/server markers.
                result[target_id_raw] = self._scrub_private_fields(p)

        return result

    def can_see_presence(self, viewer_id: int, target_id: int) -> bool:
        """
        Check if viewer can see target's real presence.

        Args:
            viewer_id: ID of the user viewing
            target_id: ID of the user being viewed

        Returns:
            True if viewer can see real presence
        """
        if int(viewer_id) == int(target_id):
            return True

        if self._relationships:
            if self._relationships.is_blocked_by_either(viewer_id, target_id):
                return False

        status = self.get_status(target_id)
        if status == UserStatus.INVISIBLE:
            return False

        return True
