"""TURN handling mixin for SignalingManager."""

from typing import Any, Optional

from ..models import TURNCredentials


class TURNMixin:
    """Mixin handling TURN credential methods."""

    _ice_builder: Any

    def get_turn_credentials(self, user_id: int) -> Optional[TURNCredentials]:
        """
        Get TURN server credentials for a user.

        Args:
            user_id: User ID

        Returns:
            TURNCredentials or None if TURN is not configured
        """
        creds = self._ice_builder.get_turn_credentials(user_id)
        return creds
