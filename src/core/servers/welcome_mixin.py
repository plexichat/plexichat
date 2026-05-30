"""Welcome screen operations mixin."""

from typing import Any, Dict, List, Optional

from src.core.base import SnowflakeID

from .models import WelcomeScreen


class WelcomeMixin:
    """Mixin for welcome screen operations.

    Provides: set_welcome_screen, get_welcome_screen, delete_welcome_screen
    """

    _onboarding_manager: Any = None

    def set_welcome_screen(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        description: Optional[str] = None,
        welcome_channels: Optional[List[Dict[str, Any]]] = None,
        enabled: bool = True,
    ) -> WelcomeScreen:
        """Set or update the welcome screen for a server."""
        return self._onboarding_manager.set_welcome_screen(
            user_id, server_id, description, welcome_channels, enabled
        )

    def get_welcome_screen(
        self, server_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[WelcomeScreen]:
        """Get the welcome screen for a server."""
        return self._onboarding_manager.get_welcome_screen(server_id, user_id)

    def delete_welcome_screen(
        self, user_id: SnowflakeID, server_id: SnowflakeID
    ) -> bool:
        """Delete the welcome screen for a server."""
        return self._onboarding_manager.delete_welcome_screen(user_id, server_id)
