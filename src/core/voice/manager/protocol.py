from typing import Any, Optional, Dict, List

from ...base import SnowflakeID
from ..models import VoiceState, VoiceChannel


class VoiceProtocol:
    _db: Any = None
    _auth: Any = None
    _servers: Any = None
    _relationships: Any = None
    _presence: Any = None

    def _get_timestamp(self) -> int:
        return 0

    def _generate_id(self) -> int:
        return 0

    def _user_exists(self, user_id: SnowflakeID) -> bool:
        return False

    def _validate_user(self, user_id: SnowflakeID) -> None:
        pass

    def _get_server_channel(self, channel_id: SnowflakeID) -> Optional[Dict[str, Any]]:
        return None

    def _is_voice_channel(self, channel_type: str) -> bool:
        return False

    def _check_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        permission: str,
        channel_id: Optional[SnowflakeID] = None,
    ) -> bool:
        return True

    def _require_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        permission: str,
        channel_id: Optional[SnowflakeID] = None,
    ) -> None:
        pass

    def _get_channel_user_count(self, channel_id: SnowflakeID) -> int:
        return 0

    def _get_channel_settings(self, channel_id: SnowflakeID) -> Dict[str, Any]:
        return {}

    def _ensure_channel_settings(self, channel_id: SnowflakeID) -> None:
        pass

    def _row_to_voice_state(self, row: Dict[str, Any]) -> VoiceState:
        raise NotImplementedError

    def _row_to_voice_channel(
        self,
        channel_row: Dict[str, Any],
        settings: Dict[str, Any],
        prefetched_counts: Optional[Dict[SnowflakeID, int]] = None,
    ) -> VoiceChannel:
        raise NotImplementedError

    def get_voice_state(self, user_id: SnowflakeID) -> Optional[VoiceState]:
        return None

    def get_channel_members(self, channel_id: SnowflakeID) -> List[Any]:
        return []

    def _on_member_joined(self, user_id: SnowflakeID, channel_id: SnowflakeID) -> None:
        pass

    def _on_member_left(self, user_id: SnowflakeID, channel_id: SnowflakeID) -> None:
        pass

    def get_voice_channel(
        self, channel_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[VoiceChannel]:
        return None

    def move_member(
        self,
        moderator_id: SnowflakeID,
        target_user_id: SnowflakeID,
        channel_id: SnowflakeID,
    ) -> VoiceState:
        raise NotImplementedError

    def get_stage(self, channel_id: SnowflakeID) -> Optional[Any]:
        return None
