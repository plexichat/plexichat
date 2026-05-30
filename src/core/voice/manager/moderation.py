import utils.logger as logger
from ...base import SnowflakeID
from ..models import VoiceState
from ..exceptions import (
    ChannelNotFoundError,
    ChannelAccessDeniedError,
    ChannelTypeError,
    UserNotInChannelError,
)


from .protocol import VoiceProtocol


class ModerationMixin(VoiceProtocol):
    def server_mute(
        self,
        moderator_id: SnowflakeID,
        target_user_id: SnowflakeID,
        server_id: SnowflakeID,
    ) -> VoiceState:
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
