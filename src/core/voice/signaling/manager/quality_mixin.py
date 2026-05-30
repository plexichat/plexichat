"""Quality handling mixin for SignalingManager."""

from typing import Any, Dict, Optional

import utils.logger as logger

from ..exceptions import NotConnectedError
from ..models import ConnectionQuality, QualityLevel, QUALITY_BITRATE_THRESHOLDS


class QualityMixin:
    """Mixin handling quality monitoring methods."""

    _connections: Dict[int, Any]

    def _get_timestamp(self) -> int: ...

    def get_connection_quality(
        self, user_id: int, channel_id: int
    ) -> ConnectionQuality:
        """
        Get connection quality metrics for a user.

        Args:
            user_id: User ID
            channel_id: Voice channel ID

        Returns:
            ConnectionQuality
        """
        connection = self._connections.get(user_id)
        if not connection:
            raise NotConnectedError(
                "User not connected to voice", user_id=user_id, channel_id=channel_id
            )

        if connection.quality:
            return connection.quality

        return ConnectionQuality(
            user_id=user_id,
            channel_id=channel_id,
            quality_level=QualityLevel.GOOD,
            bitrate=64000,
            packet_loss=0.0,
            jitter=0.0,
            round_trip_time=50,
            timestamp=self._get_timestamp(),
        )

    def update_quality_hint(
        self,
        user_id: int,
        channel_id: int,
        target_bitrate: Optional[int] = None,
        quality_level: Optional[str] = None,
    ) -> bool:
        """
        Update quality hints for a connection.

        Args:
            user_id: User ID
            channel_id: Voice channel ID
            target_bitrate: Target bitrate in bps
            quality_level: Quality level name

        Returns:
            True if updated
        """
        connection = self._connections.get(user_id)
        if not connection:
            return False

        now = self._get_timestamp()

        level = QualityLevel.GOOD
        if quality_level:
            try:
                level = QualityLevel(quality_level)
            except ValueError:
                logger.warning(
                    f"Invalid quality level: {quality_level}, defaulting to GOOD"
                )
                level = QualityLevel.GOOD

        bitrate = target_bitrate or 64000
        if not target_bitrate and quality_level:
            thresholds = QUALITY_BITRATE_THRESHOLDS.get(level, {})
            bitrate = thresholds.get("max", 64000)

        connection.quality = ConnectionQuality(
            user_id=user_id,
            channel_id=channel_id,
            quality_level=level,
            bitrate=bitrate,
            packet_loss=0.0,
            jitter=0.0,
            round_trip_time=50,
            timestamp=now,
        )
        connection.last_activity = now

        return True
