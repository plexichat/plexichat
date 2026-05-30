"""Screen share handling mixin for SignalingManager."""

from typing import Any, Dict, Optional

import utils.logger as logger

from ..exceptions import NotConnectedError, ScreenShareError
from ..models import ScreenShareState


class ScreenShareMixin:
    """Mixin handling screen share methods."""

    _voice: Optional[Any]
    _connections: Dict[int, Any]

    def _get_timestamp(self) -> int: ...

    def start_screen_share(self, user_id: int, channel_id: int) -> ScreenShareState:
        """
        Start screen sharing for a user.

        Args:
            user_id: User ID
            channel_id: Voice channel ID

        Returns:
            ScreenShareState
        """
        connection = self._connections.get(user_id)
        if not connection:
            raise NotConnectedError(
                "User not connected to voice", user_id=user_id, channel_id=channel_id
            )

        if connection.channel_id != channel_id:
            raise ScreenShareError(
                "User not in specified channel",
                user_id=user_id,
                reason="channel_mismatch",
            )

        if connection.screen_share and connection.screen_share.active:
            raise ScreenShareError(
                "Screen share already active", user_id=user_id, reason="already_sharing"
            )

        now = self._get_timestamp()
        stream_id = f"screen_{user_id}_{now}"

        screen_share = ScreenShareState(
            user_id=user_id,
            channel_id=channel_id,
            active=True,
            stream_id=stream_id,
            started_at=now,
        )

        connection.screen_share = screen_share
        connection.last_activity = now

        if self._voice:
            try:
                self._voice.set_streaming(user_id, True)
            except Exception as e:
                logger.warning(
                    f"Failed to update voice streaming state for user {user_id}: {e}"
                )

        logger.debug(f"User {user_id} started screen share")

        return screen_share

    def stop_screen_share(self, user_id: int, channel_id: int) -> bool:
        """
        Stop screen sharing for a user.

        Args:
            user_id: User ID
            channel_id: Voice channel ID

        Returns:
            True if stopped
        """
        connection = self._connections.get(user_id)
        if not connection:
            return False

        if not connection.screen_share or not connection.screen_share.active:
            return False

        connection.screen_share.active = False
        connection.last_activity = self._get_timestamp()

        if self._voice:
            try:
                self._voice.set_streaming(user_id, False)
            except Exception as e:
                logger.warning(
                    f"Failed to stop voice streaming state for user {user_id}: {e}"
                )

        logger.debug(f"User {user_id} stopped screen share")

        return True
