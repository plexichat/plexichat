from typing import Optional, List

from ...base import SnowflakeID
from ..models import VoiceChannel, VoiceRegion, DEFAULT_VOICE_REGIONS
from ..exceptions import (
    ChannelNotFoundError,
    ChannelTypeError,
    InvalidVoiceStateError,
)


from .protocol import VoiceProtocol


class SettingsMixin(VoiceProtocol):
    def set_user_limit(
        self, user_id: SnowflakeID, channel_id: SnowflakeID, limit: int
    ) -> VoiceChannel:
        channel = self._get_server_channel(channel_id)
        if not channel:
            raise ChannelNotFoundError(f"Channel {channel_id} not found")

        if not self._is_voice_channel(channel["channel_type"]):
            raise ChannelTypeError(
                "Not a voice channel",
                expected="voice or stage",
                actual=channel["channel_type"],
            )

        server_id = channel["server_id"]
        self._require_permission(user_id, server_id, "channels.manage")

        self._ensure_channel_settings(channel_id)

        self._db.execute(
            "UPDATE voice_channel_settings SET user_limit = ? WHERE channel_id = ?",
            (max(0, limit), channel_id),
        )

        result = self.get_voice_channel(channel_id, user_id)
        assert result is not None
        return result

    def set_bitrate(
        self, user_id: SnowflakeID, channel_id: SnowflakeID, bitrate: int
    ) -> VoiceChannel:
        channel = self._get_server_channel(channel_id)
        if not channel:
            raise ChannelNotFoundError(f"Channel {channel_id} not found")

        if not self._is_voice_channel(channel["channel_type"]):
            raise ChannelTypeError(
                "Not a voice channel",
                expected="voice or stage",
                actual=channel["channel_type"],
            )

        server_id = channel["server_id"]
        self._require_permission(user_id, server_id, "channels.manage")

        bitrate = max(8000, min(384000, bitrate))

        self._ensure_channel_settings(channel_id)

        self._db.execute(
            "UPDATE voice_channel_settings SET bitrate = ? WHERE channel_id = ?",
            (bitrate, channel_id),
        )

        result = self.get_voice_channel(channel_id, user_id)
        assert result is not None
        return result

    def set_voice_region(
        self, user_id: SnowflakeID, channel_id: SnowflakeID, region_id: Optional[str]
    ) -> VoiceChannel:
        channel = self._get_server_channel(channel_id)
        if not channel:
            raise ChannelNotFoundError(f"Channel {channel_id} not found")

        if not self._is_voice_channel(channel["channel_type"]):
            raise ChannelTypeError(
                "Not a voice channel",
                expected="voice or stage",
                actual=channel["channel_type"],
            )

        server_id = channel["server_id"]
        self._require_permission(user_id, server_id, "channels.manage")

        if region_id:
            valid_regions = [r.id for r in DEFAULT_VOICE_REGIONS]
            if region_id not in valid_regions:
                raise InvalidVoiceStateError(f"Invalid region: {region_id}")

        self._ensure_channel_settings(channel_id)

        self._db.execute(
            "UPDATE voice_channel_settings SET region_id = ? WHERE channel_id = ?",
            (region_id, channel_id),
        )

        result = self.get_voice_channel(channel_id, user_id)
        assert result is not None
        return result

    def get_voice_regions(self) -> List[VoiceRegion]:
        return DEFAULT_VOICE_REGIONS.copy()
