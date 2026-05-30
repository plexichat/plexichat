from typing import Optional, Dict, Any

import utils.logger as logger
from ...base import SnowflakeID
from ..models import VoiceState
from ..exceptions import ChannelNotFoundError, ChannelTypeError


from .protocol import VoiceProtocol


class AfkMixin(VoiceProtocol):
    def _get_server_settings(self, server_id: SnowflakeID) -> Dict[str, Any]:
        row = self._db.fetch_one(
            "SELECT * FROM voice_server_settings WHERE server_id = ?", (server_id,)
        )
        if row:
            return dict(row)
        return {"server_id": server_id, "afk_channel_id": None, "afk_timeout": 300}

    def _ensure_server_settings(self, server_id: SnowflakeID) -> None:
        self._db.insert_or_ignore("voice_server_settings", ["server_id"], (server_id,))

    def set_afk_channel(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_id: Optional[SnowflakeID],
    ) -> bool:
        self._require_permission(user_id, server_id, "server.manage")

        if channel_id:
            channel = self._get_server_channel(channel_id)
            if not channel or channel["server_id"] != server_id:
                raise ChannelNotFoundError(
                    f"Channel {channel_id} not found in server {server_id}"
                )
            if channel["channel_type"] != "voice":
                raise ChannelTypeError(
                    "AFK channel must be a standard voice channel",
                    expected="voice",
                    actual=channel["channel_type"],
                )

        self._ensure_server_settings(server_id)

        self._db.execute(
            "UPDATE voice_server_settings SET afk_channel_id = ? WHERE server_id = ?",
            (channel_id, server_id),
        )

        logger.debug(
            f"AFK channel for server {server_id} set to {channel_id} by {user_id}"
        )

        return True

    def set_afk_timeout(
        self, user_id: SnowflakeID, server_id: SnowflakeID, timeout: int
    ) -> bool:
        self._require_permission(user_id, server_id, "server.manage")

        timeout = max(60, min(3600, timeout))

        self._ensure_server_settings(server_id)

        self._db.execute(
            "UPDATE voice_server_settings SET afk_timeout = ? WHERE server_id = ?",
            (timeout, server_id),
        )

        return True

    def get_afk_channel(self, server_id: SnowflakeID) -> Optional[SnowflakeID]:
        settings = self._get_server_settings(server_id)
        return settings.get("afk_channel_id")

    def get_afk_timeout(self, server_id: SnowflakeID) -> int:
        settings = self._get_server_settings(server_id)
        return settings.get("afk_timeout", 300)

    def check_afk_timeout(self, user_id: SnowflakeID) -> Optional[VoiceState]:
        state = self.get_voice_state(user_id)
        if not state:
            return None

        server_id = state.server_id
        settings = self._get_server_settings(server_id)
        if not settings or not settings["afk_channel_id"]:
            return None

        if state.channel_id == settings["afk_channel_id"]:
            return None

        now = self._get_timestamp()
        timeout_ms = settings["afk_timeout"] * 1000

        if now - state.last_activity >= timeout_ms:
            logger.info(
                f"User {user_id} timed out, moving to AFK channel {settings['afk_channel_id']}"
            )
            self.move_member(user_id, user_id, settings["afk_channel_id"])
            return self.get_voice_state(user_id)

        return None
