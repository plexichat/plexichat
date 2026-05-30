from typing import Any, Optional, Dict

from ...base import SnowflakeID
from ..models import VoiceState, VoiceChannel


class VoiceProtocol:
    _db: Any = None
    _auth: Any = None
    _servers: Any = None
    _relationships: Any = None
    _presence: Any = None

    def _get_timestamp(self) -> int:
        return super()._get_timestamp()  # type: ignore[misc]

    def _generate_id(self) -> int:
        return super()._generate_id()  # type: ignore[misc]

    def _user_exists(self, user_id: SnowflakeID) -> bool:
        return super()._user_exists(user_id)  # type: ignore[misc]

    def _validate_user(self, user_id: SnowflakeID) -> None:
        super()._validate_user(user_id)  # type: ignore[misc]

    def _get_server_channel(self, channel_id: SnowflakeID) -> Optional[Dict[str, Any]]:
        return super()._get_server_channel(channel_id)  # type: ignore[misc]

    def _is_voice_channel(self, channel_type: str) -> bool:
        return super()._is_voice_channel(channel_type)  # type: ignore[misc]

    def _check_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        permission: str,
        channel_id: Optional[SnowflakeID] = None,
    ) -> bool:
        return super()._check_permission(user_id, server_id, permission, channel_id)  # type: ignore[misc]

    def _require_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        permission: str,
        channel_id: Optional[SnowflakeID] = None,
    ) -> None:
        super()._require_permission(user_id, server_id, permission, channel_id)  # type: ignore[misc]

    def _get_channel_user_count(self, channel_id: SnowflakeID) -> int:
        return super()._get_channel_user_count(channel_id)  # type: ignore[misc]

    def _get_channel_settings(self, channel_id: SnowflakeID) -> Dict[str, Any]:
        return super()._get_channel_settings(channel_id)  # type: ignore[misc]

    def _ensure_channel_settings(self, channel_id: SnowflakeID) -> None:
        super()._ensure_channel_settings(channel_id)  # type: ignore[misc]

    def _row_to_voice_state(self, row: Dict[str, Any]) -> VoiceState:
        return super()._row_to_voice_state(row)  # type: ignore[misc]

    def _row_to_voice_channel(
        self, channel_row: Dict[str, Any], settings: Dict[str, Any]
    ) -> VoiceChannel:
        return super()._row_to_voice_channel(channel_row, settings)  # type: ignore[misc]

    def get_voice_state(self, user_id: SnowflakeID) -> Optional[VoiceState]:
        return super().get_voice_state(user_id)  # type: ignore[misc]

    def get_voice_channel(
        self, channel_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[VoiceChannel]:
        return super().get_voice_channel(channel_id, user_id)  # type: ignore[misc]

    def move_member(
        self,
        moderator_id: SnowflakeID,
        target_user_id: SnowflakeID,
        channel_id: SnowflakeID,
    ) -> VoiceState:
        return super().move_member(moderator_id, target_user_id, channel_id)  # type: ignore[misc]

    def get_stage(self, channel_id: SnowflakeID) -> Optional[Any]:
        return super().get_stage(channel_id)  # type: ignore[misc]
