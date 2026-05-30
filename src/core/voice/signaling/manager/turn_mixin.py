"""TURN handling mixin for SignalingManager."""

from typing import Any

from ..models import TURNCredentials


class TURNMixin:
    """Mixin handling TURN credential methods."""

    _ice_builder: Any

    def get_turn_credentials(self, user_id: int) -> TURNCredentials:
        """
        Get TURN server credentials for a user.

        Args:
            user_id: User ID

        Returns:
            TURNCredentials
        """
        creds = self._ice_builder.get_turn_credentials(user_id)
        if not creds:
            return TURNCredentials(
                username="",
                credential="",
                urls=[],
                ttl=0,
                expires_at=0,
            )
        return creds
