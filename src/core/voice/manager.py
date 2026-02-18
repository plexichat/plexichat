"""
Voice manager - Core business logic for voice channel operations.

Handles voice state management, stage channels, and moderation
with proper validation, permission checks, and database interactions.
"""

from typing import Optional, List, Dict, Any

import utils.logger as logger
from ..base import BaseManager, SnowflakeID

from .models import (
    VoiceState,
    VoiceChannel,
    StageInstance,
    VoiceRegion,
    SpeakerRequest,
    VoiceChannelType,
    DEFAULT_VOICE_REGIONS,
)
from .exceptions import (
    VoiceError,
    ChannelNotFoundError,
    ChannelAccessDeniedError,
    ChannelFullError,
    ChannelTypeError,
    UserNotInChannelError,
    UserAlreadyInChannelError,
    StageNotFoundError,
    SpeakerRequestNotFoundError,
    SpeakerRequestExistsError,
    NotSpeakerError,
    AlreadySpeakerError,
    PermissionDeniedError,
    InvalidVoiceStateError,
    UserNotFoundError,
)


class VoiceManager(BaseManager):
    """Core voice manager handling all operations."""

    def __init__(
        self,
        db,
        auth_module=None,
        servers_module=None,
        relationships_module=None,
        presence_module=None,
    ):
        """
        Initialize the voice manager.

        Args:
            db: Database instance (must be connected)
            auth_module: Optional auth module for user verification
            servers_module: Optional servers module for channel/permission checks
            relationships_module: Optional relationships module for block checks
            presence_module: Optional presence module for activity updates
        """
        super().__init__(db, auth_module)
        self._servers = servers_module
        self._relationships = relationships_module
        self._presence = presence_module


        logger.info("Voice module initialized")

    def _validate_user(self, user_id: SnowflakeID) -> None:
        """Validate user exists."""
        if not self._user_exists(user_id):
            raise UserNotFoundError(f"User {user_id} not found")

    def _get_server_channel(self, channel_id: SnowflakeID) -> Optional[Dict[str, Any]]:
        """Get channel info from servers module or database."""
        if self._servers:
            row = self._db.fetch_one(
                "SELECT * FROM srv_channels WHERE id = ? AND deleted = 0", (channel_id,)
            )
            return dict(row) if row else None
        return None

    def _is_voice_channel(self, channel_type: str) -> bool:
        """Check if channel type is a voice channel."""
        return channel_type in ("voice", "stage")

    def _check_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        permission: str,
        channel_id: Optional[SnowflakeID] = None,
    ) -> bool:
        """Check if user has permission."""
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
        """Require a permission, raising if not granted."""
        if not self._check_permission(user_id, server_id, permission, channel_id):
            raise PermissionDeniedError(f"Missing permission: {permission}", permission)

    def _get_channel_user_count(self, channel_id: SnowflakeID) -> int:
        """Get number of users in a channel."""
        row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM voice_states WHERE channel_id = ?",
            (channel_id,),
        )
        return row["count"] if row else 0

    def _get_channel_settings(self, channel_id: SnowflakeID) -> Dict[str, Any]:
        """Get voice channel settings."""
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
        """Ensure channel settings record exists."""
        self._db.insert_or_ignore(
            "voice_channel_settings", ["channel_id"], (channel_id,)
        )

    def _row_to_voice_state(self, row: Dict[str, Any]) -> VoiceState:
        """Convert database row to VoiceState."""
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
        """Convert database rows to VoiceChannel."""
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

    # === Channel Operations ===

    def join_channel(self, user_id: SnowflakeID, channel_id: SnowflakeID) -> VoiceState:
        """
        Join a voice channel.

        Args:
            user_id: ID of the user joining
            channel_id: ID of the voice channel

        Returns:
            VoiceState for the user
        """
        self._validate_user(user_id)

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

        self._require_permission(user_id, server_id, "voice.connect", channel_id)

        existing = self.get_voice_state(user_id)
        if existing:
            if existing.channel_id == channel_id:
                raise UserAlreadyInChannelError("Already in this channel", channel_id)
            self.leave_channel(user_id)

        settings = self._get_channel_settings(channel_id)
        user_limit = settings.get("user_limit", 0)
        if user_limit > 0:
            current_count = self._get_channel_user_count(channel_id)
            if current_count >= user_limit:
                if not self._check_permission(user_id, server_id, "voice.move_members"):
                    raise ChannelFullError(
                        f"Channel is full ({current_count}/{user_limit})",
                        limit=user_limit,
                        current=current_count,
                    )

        now = self._get_timestamp()
        suppress = channel["channel_type"] == "stage"

        self._db.execute(
            """INSERT INTO voice_states 
               (user_id, channel_id, server_id, suppress, joined_at, last_activity)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, channel_id, server_id, 1 if suppress else 0, now, now),
        )

        logger.debug(f"User {user_id} joined voice channel {channel_id}")

        result = self.get_voice_state(user_id)
        assert result is not None
        return result

    def leave_channel(self, user_id: SnowflakeID) -> bool:
        """
        Leave current voice channel.

        Args:
            user_id: ID of the user leaving

        Returns:
            True if successfully left
        """
        state = self.get_voice_state(user_id)
        if not state:
            raise UserNotInChannelError(f"User {user_id} is not in a voice channel")

        self._db.execute(
            "DELETE FROM voice_speaker_requests WHERE user_id = ?", (user_id,)
        )

        self._db.execute("DELETE FROM voice_states WHERE user_id = ?", (user_id,))

        logger.debug(f"User {user_id} left voice channel {state.channel_id}")

        return True

    def move_to_channel(
        self, user_id: SnowflakeID, channel_id: SnowflakeID
    ) -> VoiceState:
        """
        Move to a different voice channel.

        Args:
            user_id: ID of the user moving
            channel_id: ID of the target channel

        Returns:
            New VoiceState
        """
        current = self.get_voice_state(user_id)
        if current:
            self.leave_channel(user_id)

        return self.join_channel(user_id, channel_id)

    def get_channel_users(self, channel_id: SnowflakeID) -> List[VoiceState]:
        """Get all users in a voice channel."""
        rows = self._db.fetch_all(
            "SELECT * FROM voice_states WHERE channel_id = ?", (channel_id,)
        )
        return [self._row_to_voice_state(row) for row in rows]

    def get_voice_channel(
        self, channel_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[VoiceChannel]:
        """Get voice channel info."""
        channel = self._get_server_channel(channel_id)
        if not channel:
            return None

        if not self._is_voice_channel(channel["channel_type"]):
            return None

        if self._servers:
            if not self._servers.has_permission(
                user_id, channel["server_id"], "channels.view", channel_id
            ):
                return None

        settings = self._get_channel_settings(channel_id)
        return self._row_to_voice_channel(channel, settings)

    def get_voice_channels(
        self, user_id: SnowflakeID, server_id: SnowflakeID
    ) -> List[VoiceChannel]:
        """Get all voice channels in a server."""
        rows = self._db.fetch_all(
            """SELECT * FROM srv_channels 
               WHERE server_id = ? AND channel_type IN ('voice', 'stage') AND deleted = 0
               ORDER BY position""",
            (server_id,),
        )

        channels = []
        for row in rows:
            if self._servers:
                if not self._servers.has_permission(
                    user_id, server_id, "channels.view", row["id"]
                ):
                    continue
            settings = self._get_channel_settings(row["id"])
            channels.append(self._row_to_voice_channel(dict(row), settings))

        return channels

    # === Voice State Operations ===

    def get_voice_state(self, user_id: SnowflakeID) -> Optional[VoiceState]:
        """Get user's current voice state."""
        row = self._db.fetch_one(
            "SELECT * FROM voice_states WHERE user_id = ?", (user_id,)
        )
        return self._row_to_voice_state(row) if row else None

    def set_self_mute(self, user_id: SnowflakeID, muted: bool) -> VoiceState:
        """Set self-mute state."""
        state = self.get_voice_state(user_id)
        if not state:
            raise UserNotInChannelError(f"User {user_id} is not in a voice channel")

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE voice_states SET self_mute = ?, last_activity = ? WHERE user_id = ?",
            (1 if muted else 0, now, user_id),
        )

        result = self.get_voice_state(user_id)
        assert result is not None
        return result

    def set_self_deaf(self, user_id: SnowflakeID, deafened: bool) -> VoiceState:
        """Set self-deaf state."""
        state = self.get_voice_state(user_id)
        if not state:
            raise UserNotInChannelError(f"User {user_id} is not in a voice channel")

        now = self._get_timestamp()
        mute_val = 1 if deafened else state.self_mute
        self._db.execute(
            "UPDATE voice_states SET self_deaf = ?, self_mute = ?, last_activity = ? WHERE user_id = ?",
            (1 if deafened else 0, mute_val, now, user_id),
        )

        result = self.get_voice_state(user_id)
        assert result is not None
        return result

    def set_streaming(self, user_id: SnowflakeID, streaming: bool) -> VoiceState:
        """Set streaming (screen share) state."""
        state = self.get_voice_state(user_id)
        if not state:
            raise UserNotInChannelError(f"User {user_id} is not in a voice channel")

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE voice_states SET streaming = ?, last_activity = ? WHERE user_id = ?",
            (1 if streaming else 0, now, user_id),
        )

        result = self.get_voice_state(user_id)
        assert result is not None
        return result

    def set_video(self, user_id: SnowflakeID, video: bool) -> VoiceState:
        """Set video (camera) state."""
        state = self.get_voice_state(user_id)
        if not state:
            raise UserNotInChannelError(f"User {user_id} is not in a voice channel")

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE voice_states SET video = ?, last_activity = ? WHERE user_id = ?",
            (1 if video else 0, now, user_id),
        )

        result = self.get_voice_state(user_id)
        assert result is not None
        return result

    def update_voice_state(
        self,
        user_id: SnowflakeID,
        self_mute: Optional[bool] = None,
        self_deaf: Optional[bool] = None,
        streaming: Optional[bool] = None,
        video: Optional[bool] = None,
    ) -> VoiceState:
        """Update multiple voice state properties at once."""
        state = self.get_voice_state(user_id)
        if not state:
            raise UserNotInChannelError(f"User {user_id} is not in a voice channel")

        updates = []
        params = []

        if self_mute is not None:
            updates.append("self_mute = ?")
            params.append(1 if self_mute else 0)

        if self_deaf is not None:
            updates.append("self_deaf = ?")
            params.append(1 if self_deaf else 0)
            if self_deaf:
                updates.append("self_mute = ?")
                params.append(1)

        if streaming is not None:
            updates.append("streaming = ?")
            params.append(1 if streaming else 0)

        if video is not None:
            updates.append("video = ?")
            params.append(1 if video else 0)

        if updates:
            now = self._get_timestamp()
            updates.append("last_activity = ?")
            params.append(now)
            params.append(user_id)

            self._db.execute(
                f"UPDATE voice_states SET {', '.join(updates)} WHERE user_id = ?",
                tuple(params),
            )

        result = self.get_voice_state(user_id)
        assert result is not None
        return result

    # === Server Moderation ===

    def server_mute(
        self,
        moderator_id: SnowflakeID,
        target_user_id: SnowflakeID,
        server_id: SnowflakeID,
    ) -> VoiceState:
        """Server mute a user (moderator action)."""
        self._require_permission(moderator_id, server_id, "voice.mute_members")

        state = self.get_voice_state(target_user_id)
        if not state:
            raise UserNotInChannelError(
                f"User {target_user_id} is not in a voice channel"
            )

        if state.server_id != server_id:
            raise ChannelAccessDeniedError("User is not in this server's voice channel")

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE voice_states SET server_mute = 1, last_activity = ? WHERE user_id = ?",
            (now, target_user_id),
        )

        logger.debug(f"User {target_user_id} server muted by {moderator_id}")

        result = self.get_voice_state(target_user_id)
        assert result is not None
        return result

    def server_unmute(
        self,
        moderator_id: SnowflakeID,
        target_user_id: SnowflakeID,
        server_id: SnowflakeID,
    ) -> VoiceState:
        """Server unmute a user (moderator action)."""
        self._require_permission(moderator_id, server_id, "voice.mute_members")

        state = self.get_voice_state(target_user_id)
        if not state:
            raise UserNotInChannelError(
                f"User {target_user_id} is not in a voice channel"
            )

        if state.server_id != server_id:
            raise ChannelAccessDeniedError("User is not in this server's voice channel")

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE voice_states SET server_mute = 0, last_activity = ? WHERE user_id = ?",
            (now, target_user_id),
        )

        result = self.get_voice_state(target_user_id)
        assert result is not None
        return result

    def server_deaf(
        self,
        moderator_id: SnowflakeID,
        target_user_id: SnowflakeID,
        server_id: SnowflakeID,
    ) -> VoiceState:
        """Server deafen a user (moderator action)."""
        self._require_permission(moderator_id, server_id, "voice.deafen_members")

        state = self.get_voice_state(target_user_id)
        if not state:
            raise UserNotInChannelError(
                f"User {target_user_id} is not in a voice channel"
            )

        if state.server_id != server_id:
            raise ChannelAccessDeniedError("User is not in this server's voice channel")

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE voice_states SET server_deaf = 1, server_mute = 1, last_activity = ? WHERE user_id = ?",
            (now, target_user_id),
        )

        logger.debug(f"User {target_user_id} server deafened by {moderator_id}")

        result = self.get_voice_state(target_user_id)
        assert result is not None
        return result

    def server_undeaf(
        self,
        moderator_id: SnowflakeID,
        target_user_id: SnowflakeID,
        server_id: SnowflakeID,
    ) -> VoiceState:
        """Server undeafen a user (moderator action)."""
        self._require_permission(moderator_id, server_id, "voice.deafen_members")

        state = self.get_voice_state(target_user_id)
        if not state:
            raise UserNotInChannelError(
                f"User {target_user_id} is not in a voice channel"
            )

        if state.server_id != server_id:
            raise ChannelAccessDeniedError("User is not in this server's voice channel")

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE voice_states SET server_deaf = 0, last_activity = ? WHERE user_id = ?",
            (now, target_user_id),
        )

        result = self.get_voice_state(target_user_id)
        assert result is not None
        return result

    def move_member(
        self,
        moderator_id: SnowflakeID,
        target_user_id: SnowflakeID,
        channel_id: SnowflakeID,
    ) -> VoiceState:
        """Move a member to a different voice channel (moderator action)."""
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
        self._require_permission(moderator_id, server_id, "voice.move_members")

        state = self.get_voice_state(target_user_id)
        if not state:
            raise UserNotInChannelError(
                f"User {target_user_id} is not in a voice channel"
            )

        self._db.execute(
            "DELETE FROM voice_speaker_requests WHERE user_id = ?", (target_user_id,)
        )

        now = self._get_timestamp()
        suppress = channel["channel_type"] == "stage"

        self._db.execute(
            """UPDATE voice_states 
               SET channel_id = ?, server_id = ?, suppress = ?, last_activity = ?
               WHERE user_id = ?""",
            (channel_id, server_id, 1 if suppress else 0, now, target_user_id),
        )

        logger.debug(
            f"User {target_user_id} moved to channel {channel_id} by {moderator_id}"
        )

        result = self.get_voice_state(target_user_id)
        assert result is not None
        return result

    def disconnect_member(
        self,
        moderator_id: SnowflakeID,
        target_user_id: SnowflakeID,
        server_id: SnowflakeID,
    ) -> bool:
        """Disconnect a member from voice (moderator action)."""
        self._require_permission(moderator_id, server_id, "voice.move_members")

        state = self.get_voice_state(target_user_id)
        if not state:
            raise UserNotInChannelError(
                f"User {target_user_id} is not in a voice channel"
            )

        if state.server_id != server_id:
            raise ChannelAccessDeniedError("User is not in this server's voice channel")

        self._db.execute(
            "DELETE FROM voice_speaker_requests WHERE user_id = ?", (target_user_id,)
        )

        self._db.execute(
            "DELETE FROM voice_states WHERE user_id = ?", (target_user_id,)
        )

        logger.debug(f"User {target_user_id} disconnected from voice by {moderator_id}")

        return True

    # === Stage Channel Operations ===

    def start_stage(
        self, user_id: SnowflakeID, channel_id: SnowflakeID, topic: str
    ) -> StageInstance:
        """
        Start a stage instance in a stage channel.

        Args:
            user_id: ID of the user starting the stage
            channel_id: ID of the stage channel
            topic: Topic of the stage

        Returns:
            StageInstance
        """
        channel = self._get_server_channel(channel_id)
        if not channel:
            raise ChannelNotFoundError(f"Channel {channel_id} not found")

        if channel["channel_type"] != "stage":
            raise ChannelTypeError(
                "Not a stage channel", expected="stage", actual=channel["channel_type"]
            )

        server_id = channel["server_id"]
        self._require_permission(user_id, server_id, "voice.speak", channel_id)

        existing = self.get_stage(channel_id)
        if existing:
            raise VoiceError("Stage already active in this channel")

        now = self._get_timestamp()
        stage_id = self._generate_id()

        self._db.execute(
            """INSERT INTO voice_stage_instances 
               (id, channel_id, server_id, topic, started_by, started_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (stage_id, channel_id, server_id, topic, user_id, now),
        )

        state = self.get_voice_state(user_id)
        if state and state.channel_id == channel_id:
            self._db.execute(
                "UPDATE voice_states SET suppress = 0, last_activity = ? WHERE user_id = ?",
                (now, user_id),
            )

        logger.debug(f"Stage started in channel {channel_id} by user {user_id}")

        result = self.get_stage(channel_id)
        assert result is not None
        return result

    def end_stage(self, user_id: SnowflakeID, channel_id: SnowflakeID) -> bool:
        """
        End a stage instance.

        Args:
            user_id: ID of the user ending the stage
            channel_id: ID of the stage channel

        Returns:
            True if ended successfully
        """
        stage = self.get_stage(channel_id)
        if not stage:
            raise StageNotFoundError(f"No active stage in channel {channel_id}")

        channel = self._get_server_channel(channel_id)
        server_id = channel["server_id"] if channel else stage.server_id

        is_starter = stage.started_by == user_id
        has_permission = self._check_permission(
            user_id, server_id, "voice.mute_members", channel_id
        )

        if not is_starter and not has_permission:
            raise PermissionDeniedError("Cannot end stage", "voice.mute_members")

        self._db.execute(
            "DELETE FROM voice_speaker_requests WHERE channel_id = ?", (channel_id,)
        )

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE voice_states SET suppress = 1, last_activity = ? WHERE channel_id = ?",
            (now, channel_id),
        )

        self._db.execute(
            "DELETE FROM voice_stage_instances WHERE channel_id = ?", (channel_id,)
        )

        logger.debug(f"Stage ended in channel {channel_id} by user {user_id}")

        return True

    def get_stage(self, channel_id: SnowflakeID) -> Optional[StageInstance]:
        """Get active stage instance for a channel."""
        row = self._db.fetch_one(
            "SELECT * FROM voice_stage_instances WHERE channel_id = ?", (channel_id,)
        )

        if not row:
            return None

        speaker_count = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM voice_states WHERE channel_id = ? AND suppress = 0",
            (channel_id,),
        )
        audience_count = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM voice_states WHERE channel_id = ? AND suppress = 1",
            (channel_id,),
        )

        return StageInstance(
            id=row["id"],
            channel_id=row["channel_id"],
            server_id=row["server_id"],
            topic=row["topic"],
            started_by=row["started_by"],
            started_at=row["started_at"],
            speaker_count=speaker_count["count"] if speaker_count else 0,
            audience_count=audience_count["count"] if audience_count else 0,
        )

    def request_to_speak(
        self, user_id: SnowflakeID, channel_id: SnowflakeID
    ) -> SpeakerRequest:
        """
        Request to speak in a stage channel (raise hand).

        Args:
            user_id: ID of the user requesting
            channel_id: ID of the stage channel

        Returns:
            SpeakerRequest
        """
        state = self.get_voice_state(user_id)
        if not state or state.channel_id != channel_id:
            raise UserNotInChannelError(
                f"User {user_id} is not in channel {channel_id}"
            )

        stage = self.get_stage(channel_id)
        if not stage:
            raise StageNotFoundError(f"No active stage in channel {channel_id}")

        if not state.suppress:
            raise AlreadySpeakerError("User is already a speaker")

        existing = self._db.fetch_one(
            "SELECT * FROM voice_speaker_requests WHERE user_id = ? AND channel_id = ?",
            (user_id, channel_id),
        )
        if existing:
            raise SpeakerRequestExistsError("Speaker request already exists")

        now = self._get_timestamp()
        request_id = self._generate_id()

        self._db.execute(
            """INSERT INTO voice_speaker_requests (id, user_id, channel_id, requested_at)
               VALUES (?, ?, ?, ?)""",
            (request_id, user_id, channel_id, now),
        )

        logger.debug(f"User {user_id} requested to speak in channel {channel_id}")

        return SpeakerRequest(
            id=request_id,
            user_id=user_id,
            channel_id=channel_id,
            requested_at=now,
        )

    def cancel_speak_request(
        self, user_id: SnowflakeID, channel_id: SnowflakeID
    ) -> bool:
        """
        Cancel a request to speak.

        Args:
            user_id: ID of the user canceling
            channel_id: ID of the stage channel

        Returns:
            True if canceled
        """
        existing = self._db.fetch_one(
            "SELECT * FROM voice_speaker_requests WHERE user_id = ? AND channel_id = ?",
            (user_id, channel_id),
        )
        if not existing:
            raise SpeakerRequestNotFoundError("No speaker request found")

        self._db.execute(
            "DELETE FROM voice_speaker_requests WHERE user_id = ? AND channel_id = ?",
            (user_id, channel_id),
        )

        return True

    def invite_to_speak(
        self,
        moderator_id: SnowflakeID,
        target_user_id: SnowflakeID,
        channel_id: SnowflakeID,
    ) -> VoiceState:
        """
        Invite a user to speak in a stage channel.

        Args:
            moderator_id: ID of the moderator inviting
            target_user_id: ID of the user being invited
            channel_id: ID of the stage channel

        Returns:
            Updated VoiceState
        """
        channel = self._get_server_channel(channel_id)
        if not channel:
            raise ChannelNotFoundError(f"Channel {channel_id} not found")

        if channel["channel_type"] != "stage":
            raise ChannelTypeError(
                "Not a stage channel", expected="stage", actual=channel["channel_type"]
            )

        server_id = channel["server_id"]
        self._require_permission(
            moderator_id, server_id, "voice.mute_members", channel_id
        )

        state = self.get_voice_state(target_user_id)
        if not state or state.channel_id != channel_id:
            raise UserNotInChannelError(
                f"User {target_user_id} is not in channel {channel_id}"
            )

        if not state.suppress:
            raise AlreadySpeakerError("User is already a speaker")

        self._db.execute(
            "DELETE FROM voice_speaker_requests WHERE user_id = ? AND channel_id = ?",
            (target_user_id, channel_id),
        )

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE voice_states SET suppress = 0, last_activity = ? WHERE user_id = ?",
            (now, target_user_id),
        )

        logger.debug(f"User {target_user_id} invited to speak by {moderator_id}")

        result = self.get_voice_state(target_user_id)
        assert result is not None
        return result

    def move_to_audience(
        self,
        moderator_id: SnowflakeID,
        target_user_id: SnowflakeID,
        channel_id: SnowflakeID,
    ) -> VoiceState:
        """
        Move a speaker to audience in a stage channel.

        Args:
            moderator_id: ID of the moderator
            target_user_id: ID of the user being moved
            channel_id: ID of the stage channel

        Returns:
            Updated VoiceState
        """
        channel = self._get_server_channel(channel_id)
        if not channel:
            raise ChannelNotFoundError(f"Channel {channel_id} not found")

        if channel["channel_type"] != "stage":
            raise ChannelTypeError(
                "Not a stage channel", expected="stage", actual=channel["channel_type"]
            )

        server_id = channel["server_id"]

        is_self = moderator_id == target_user_id
        if not is_self:
            self._require_permission(
                moderator_id, server_id, "voice.mute_members", channel_id
            )

        state = self.get_voice_state(target_user_id)
        if not state or state.channel_id != channel_id:
            raise UserNotInChannelError(
                f"User {target_user_id} is not in channel {channel_id}"
            )

        if state.suppress:
            raise NotSpeakerError("User is not a speaker")

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE voice_states SET suppress = 1, last_activity = ? WHERE user_id = ?",
            (now, target_user_id),
        )

        logger.debug(f"User {target_user_id} moved to audience by {moderator_id}")

        result = self.get_voice_state(target_user_id)
        assert result is not None
        return result

    def get_speaker_requests(self, channel_id: SnowflakeID) -> List[SpeakerRequest]:
        """Get all pending speaker requests for a stage channel."""
        rows = self._db.fetch_all(
            "SELECT * FROM voice_speaker_requests WHERE channel_id = ? ORDER BY requested_at",
            (channel_id,),
        )

        return [
            SpeakerRequest(
                id=row["id"],
                user_id=row["user_id"],
                channel_id=row["channel_id"],
                requested_at=row["requested_at"],
            )
            for row in rows
        ]

    def get_speakers(self, channel_id: SnowflakeID) -> List[VoiceState]:
        """Get all speakers in a stage channel."""
        rows = self._db.fetch_all(
            "SELECT * FROM voice_states WHERE channel_id = ? AND suppress = 0",
            (channel_id,),
        )
        return [self._row_to_voice_state(row) for row in rows]

    def get_audience(self, channel_id: SnowflakeID) -> List[VoiceState]:
        """Get all audience members in a stage channel."""
        rows = self._db.fetch_all(
            "SELECT * FROM voice_states WHERE channel_id = ? AND suppress = 1",
            (channel_id,),
        )
        return [self._row_to_voice_state(row) for row in rows]

    # === Channel Settings ===

    def set_user_limit(
        self, user_id: SnowflakeID, channel_id: SnowflakeID, limit: int
    ) -> VoiceChannel:
        """
        Set user limit for a voice channel (0 = unlimited).

        Args:
            user_id: ID of the user setting the limit
            channel_id: ID of the voice channel
            limit: User limit (0 = unlimited)

        Returns:
            Updated VoiceChannel
        """
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
        """
        Set bitrate for a voice channel.

        Args:
            user_id: ID of the user setting the bitrate
            channel_id: ID of the voice channel
            bitrate: Bitrate in bits per second (8000-384000)

        Returns:
            Updated VoiceChannel
        """
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
        """
        Set voice region for a channel (None = automatic).

        Args:
            user_id: ID of the user setting the region
            channel_id: ID of the voice channel
            region_id: Region ID or None for automatic

        Returns:
            Updated VoiceChannel
        """
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
        """Get available voice regions."""
        return DEFAULT_VOICE_REGIONS.copy()

    # === AFK Features ===

    def _get_server_settings(self, server_id: SnowflakeID) -> Dict[str, Any]:
        """Get voice server settings."""
        row = self._db.fetch_one(
            "SELECT * FROM voice_server_settings WHERE server_id = ?", (server_id,)
        )
        if row:
            return dict(row)
        return {"server_id": server_id, "afk_channel_id": None, "afk_timeout": 300}

    def _ensure_server_settings(self, server_id: SnowflakeID) -> None:
        """Ensure voice server settings record exists."""
        self._db.insert_or_ignore("voice_server_settings", ["server_id"], (server_id,))

    def set_afk_channel(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_id: Optional[SnowflakeID],
    ) -> bool:
        """
        Set the AFK channel for a server.

        Args:
            user_id: ID of the user setting the AFK channel
            server_id: ID of the server
            channel_id: ID of the voice channel to use for AFK, or None

        Returns:
            True if set successfully
        """
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
        """
        Set the AFK timeout for a server in seconds.

        Args:
            user_id: ID of the user setting the timeout
            server_id: ID of the server
            timeout: Timeout in seconds (60-3600)

        Returns:
            True if set successfully
        """
        self._require_permission(user_id, server_id, "server.manage")

        timeout = max(60, min(3600, timeout))

        self._ensure_server_settings(server_id)

        self._db.execute(
            "UPDATE voice_server_settings SET afk_timeout = ? WHERE server_id = ?",
            (timeout, server_id),
        )

        return True

    def get_afk_channel(self, server_id: SnowflakeID) -> Optional[SnowflakeID]:
        """
        Get the AFK channel ID for a server.

        Args:
            server_id: ID of the server

        Returns:
            Channel ID or None
        """
        settings = self._get_server_settings(server_id)
        return settings.get("afk_channel_id")

    def get_afk_timeout(self, server_id: SnowflakeID) -> int:
        """
        Get the AFK timeout for a server in seconds.

        Args:
            server_id: ID of the server

        Returns:
            Timeout in seconds
        """
        settings = self._get_server_settings(server_id)
        return settings.get("afk_timeout", 300)

    def check_afk_timeout(self, user_id: SnowflakeID) -> bool:
        """
        Check if a user has timed out and move them to AFK if needed.

        Returns:
            True if moved to AFK
        """
        state = self.get_voice_state(user_id)
        if not state:
            return False

        server_id = state.server_id
        settings = self._get_server_settings(server_id)
        if not settings or not settings["afk_channel_id"]:
            return False

        if state.channel_id == settings["afk_channel_id"]:
            return False

        now = self._get_timestamp()
        timeout_ms = settings["afk_timeout"] * 1000

        if now - state.last_activity >= timeout_ms:
            logger.info(
                f"User {user_id} timed out, moving to AFK channel {settings['afk_channel_id']}"
            )
            self.move_member(user_id, user_id, settings["afk_channel_id"])
            return True

        return False

    # === User Voice State Queries ===

    def is_user_in_voice(self, user_id: SnowflakeID) -> bool:
        """Check if a user is currently in a voice channel."""
        row = self._db.fetch_one(
            "SELECT 1 FROM voice_states WHERE user_id = ?", (user_id,)
        )
        return row is not None

    def get_user_channel(self, user_id: SnowflakeID) -> Optional[SnowflakeID]:
        """Get the ID of the channel the user is in."""
        state = self.get_voice_state(user_id)
        return state.channel_id if state else None

    def get_channel_members(self, channel_id: SnowflakeID) -> List[SnowflakeID]:
        """Get IDs of all members in a channel."""
        rows = self._db.fetch_all(
            "SELECT user_id FROM voice_states WHERE channel_id = ?", (channel_id,)
        )
        return [row["user_id"] for row in rows]
