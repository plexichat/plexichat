from typing import Optional, List, Dict, Any

import utils.logger as logger
from ...base import BaseManager, SnowflakeID
from ..models import (
    VoiceState,
    VoiceChannel,
    VoiceChannelType,
)
from ..exceptions import (
    PermissionDeniedError,
    UserNotFoundError,
)
from ..queries import (
    is_user_in_voice as _is_user_in_voice,
    get_user_channel as _get_user_channel,
    get_channel_members as _get_channel_members,
)
from .channels import ChannelOpsMixin
from .state import StateMixin
from .moderation import ModerationMixin
from .stages import StageOpsMixin
from .settings import SettingsMixin
from .afk import AfkMixin


class VoiceManager(
    ChannelOpsMixin,
    StateMixin,
    ModerationMixin,
    StageOpsMixin,
    SettingsMixin,
    AfkMixin,
    BaseManager,
):
    def __init__(
        self,
        db,
        auth_module=None,
        servers_module=None,
        relationships_module=None,
        presence_module=None,
    ):
        super().__init__(db, auth_module)
        self._servers = servers_module
        self._relationships = relationships_module
        self._presence = presence_module

        logger.info("Voice module initialized")

    # === Shared Helpers ===

    def _validate_user(self, user_id: SnowflakeID) -> None:
        if not self._user_exists(user_id):
            raise UserNotFoundError(f"User {user_id} not found")

    def _get_server_channel(self, channel_id: SnowflakeID) -> Optional[Dict[str, Any]]:
        if self._servers:
            row = self._db.fetch_one(
                "SELECT * FROM srv_channels WHERE id = ? AND deleted = 0", (channel_id,)
            )
            return dict(row) if row else None
        return None

    def _is_voice_channel(self, channel_type: str) -> bool:
        return channel_type in ("voice", "stage")

    def _check_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        permission: str,
        channel_id: Optional[SnowflakeID] = None,
    ) -> bool:
        if self._servers:
            return self._servers.has_permission(
                user_id, server_id, permission, channel_id
            )
        return True

    def _require_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        permission: str,
        channel_id: Optional[SnowflakeID] = None,
    ) -> None:
        if not self._check_permission(user_id, server_id, permission, channel_id):
            raise PermissionDeniedError(f"Missing permission: {permission}", permission)

    def _get_channel_user_count(self, channel_id: SnowflakeID) -> int:
        row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM voice_states WHERE channel_id = ?",
            (channel_id,),
        )
        return row["count"] if row else 0

    def _get_channel_settings(self, channel_id: SnowflakeID) -> Dict[str, Any]:
        row = self._db.fetch_one(
            "SELECT * FROM voice_channel_settings WHERE channel_id = ?", (channel_id,)
        )
        if row:
            return dict(row)
        return {
            "channel_id": channel_id,
            "user_limit": 0,
            "bitrate": 64000,
            "region_id": None,
        }

    def _ensure_channel_settings(self, channel_id: SnowflakeID) -> None:
        self._db.insert_or_ignore(
            "voice_channel_settings", ["channel_id"], (channel_id,)
        )

    def _row_to_voice_state(self, row: Dict[str, Any]) -> VoiceState:
        return VoiceState(
            user_id=row["user_id"],
            channel_id=row["channel_id"],
            server_id=row["server_id"],
            self_mute=bool(row["self_mute"]),
            self_deaf=bool(row["self_deaf"]),
            server_mute=bool(row["server_mute"]),
            server_deaf=bool(row["server_deaf"]),
            suppress=bool(row["suppress"]),
            streaming=bool(row["streaming"]),
            video=bool(row["video"]),
            joined_at=row["joined_at"],
            last_activity=row["last_activity"],
        )

    def _row_to_voice_channel(
        self, channel_row: Dict[str, Any], settings: Dict[str, Any]
    ) -> VoiceChannel:
        channel_type = (
            VoiceChannelType.STAGE
            if channel_row["channel_type"] == "stage"
            else VoiceChannelType.VOICE
        )
        user_count = self._get_channel_user_count(channel_row["id"])

        return VoiceChannel(
            id=channel_row["id"],
            server_id=channel_row["server_id"],
            name=channel_row["name"],
            channel_type=channel_type,
            user_limit=settings.get("user_limit", 0),
            bitrate=settings.get("bitrate", 64000),
            region_id=settings.get("region_id"),
            position=channel_row.get("position", 0),
            category_id=channel_row.get("category_id"),
            user_count=user_count,
            created_at=channel_row.get("created_at", 0),
            updated_at=channel_row.get("updated_at", 0),
        )

    # === User Voice State Queries ===

    def is_user_in_voice(self, user_id: SnowflakeID) -> bool:
        return _is_user_in_voice(self._db, user_id)

    def get_user_channel(self, user_id: SnowflakeID) -> Optional[SnowflakeID]:
        return _get_user_channel(self._db, user_id)

    def get_channel_members(self, channel_id: SnowflakeID) -> List[SnowflakeID]:
        return _get_channel_members(self._db, channel_id)
