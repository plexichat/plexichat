"""Screen share handling mixin for SignalingManager."""

from typing import TYPE_CHECKING, Any, Dict, Optional

import utils.logger as logger

from ..exceptions import NotConnectedError, ScreenShareError
from ..models import ScreenShareState


class ScreenShareMixin:
    """Mixin handling screen share methods."""

    if TYPE_CHECKING:

        def _get_timestamp(self) -> int: ...
        def _voice_manager(self) -> Optional[Any]: ...

    _voice: Optional[Any]
    _connections: Dict[int, Any]

    def _require_stream_permission(self, user_id: int, channel_id: int) -> None:
        """Enforce ``voice.speak`` for stream-capable operations (M7).

        Raises PermissionDeniedError when the voice module is wired and the
        user lacks the permission; fails open only when the underlying
        module cannot resolve the channel (deferring to the caller's own
        channel lookup).
        """
        manager = self._voice_manager()
        if manager is None:
            return

        get_channel = getattr(manager, "_get_server_channel", None)
        require_permission = getattr(manager, "_require_permission", None)
        if get_channel is None or require_permission is None:
            return

        try:
            channel = get_channel(channel_id)
        except Exception as e:
            logger.warning(
                f"Could not resolve channel {channel_id} for screen share "
                f"permission check: {e}"
            )
            return

        if not channel:
            return

        server_id = channel.get("server_id")
        if server_id is None:
            return

        require_permission(user_id, server_id, "voice.speak", channel_id)

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

        # SECURITY (Agent 34 M7): starting a screen share is a stream/speak
        # capability and previously had NO permission check at all. Require
        # voice.speak in the channel's server before allowing it.
        self._require_stream_permission(user_id, channel_id)

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

        # SECURITY (Agent 34 M7): stop must operate on the bound channel only;
        # a mismatched channel id previously still tore down the share.
        if connection.channel_id != channel_id:
            logger.warning(
                f"Channel mismatch stopping screen share for user {user_id}: "
                f"bound={connection.channel_id} requested={channel_id}"
            )
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
