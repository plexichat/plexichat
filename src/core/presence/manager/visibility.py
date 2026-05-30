"""Visibility operations mixin.

Handles presence visibility rules including invisible mode,
block relationships, and efficient bulk visibility checks.
"""

from typing import Dict, List

from .base import PresenceManagerBase
from ..models import Presence, UserStatus


class VisibilityMixin(PresenceManagerBase):
    """Mixin providing presence visibility operations."""

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

        if presence.status == UserStatus.INVISIBLE:
            return Presence(
                user_id=target_id,
                status=UserStatus.OFFLINE,
                custom_status=presence.custom_status,
                activity=None,
                last_seen=presence.last_seen,
                updated_at=presence.updated_at,
            )

        return presence

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

        blocked_ids: set[int] = set()
        blocked_by_ids: set[int] = set()
        if self._relationships:
            try:
                blocked_ids = set(
                    int(uid)
                    for uid in self._relationships.get_blocked_user_ids(viewer_id)
                )
                blocked_by_ids = (
                    set(
                        int(uid)
                        for uid in self._relationships.get_all_blocked_ids(viewer_id)
                    )
                    - blocked_ids
                )
            except Exception:
                pass

        for p in presences:
            target_id_raw = p.user_id
            target_id = int(target_id_raw)

            if int(viewer_id) == target_id:
                result[target_id_raw] = p
                continue

            is_blocked = (target_id in blocked_ids) or (target_id in blocked_by_ids)
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

            if p.status == UserStatus.INVISIBLE:
                result[target_id_raw] = Presence(
                    user_id=target_id_raw,
                    status=UserStatus.OFFLINE,
                    custom_status=p.custom_status,
                    activity=None,
                    last_seen=p.last_seen,
                    updated_at=p.updated_at,
                )
            else:
                result[target_id_raw] = p

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
        if viewer_id == target_id:
            return True

        if self._relationships:
            if self._relationships.is_blocked_by_either(viewer_id, target_id):
                return False

        status = self.get_status(target_id)
        if status == UserStatus.INVISIBLE:
            return False

        return True
