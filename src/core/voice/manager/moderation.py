import utils.logger as logger
from ...base import SnowflakeID
from ..models import VoiceState
from ..exceptions import (
    ChannelNotFoundError,
    ChannelAccessDeniedError,
    ChannelFullError,
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

        # Agent 34 M3: moving a member into a channel they cannot connect to
        # (or that is full) must fail just like a normal join_channel.
        self._require_permission(target_user_id, server_id, "voice.connect", channel_id)

        settings = self._get_channel_settings(channel_id)
        user_limit = settings.get("user_limit", 0)
        if user_limit > 0 and state.channel_id != channel_id:
            current_count = self._get_channel_user_count(channel_id)
            if current_count >= user_limit:
                raise ChannelFullError(
                    f"Channel is full ({current_count}/{user_limit})",
                    limit=user_limit,
                    current=current_count,
                )

        # Scope the cleanup to the channel being LEFT so pending speaker
        # requests in unrelated voice channels are preserved.
        old_channel_id = state.channel_id
        self._db.execute(
            "DELETE FROM voice_speaker_requests WHERE user_id = ? AND channel_id = ?",
            (target_user_id, old_channel_id),
        )

        now = self._get_timestamp()
        suppress = channel["channel_type"] == "stage"

        self._db.execute(
            """UPDATE voice_states
               SET channel_id = ?, server_id = ?, suppress = ?, last_activity = ?
               WHERE user_id = ?""",
            (channel_id, server_id, 1 if suppress else 0, now, target_user_id),
        )

        # A moderation move is a leave+join: fire the same call-lifecycle
        # hooks so the old channel's call is ended (and its participant
        # snapshot pruned) and the new channel's call starts. Run them AFTER
        # the UPDATE so each hook observes the new membership.
        if old_channel_id != channel_id:
            self._on_member_left(target_user_id, old_channel_id)
            self._on_member_joined(target_user_id, channel_id)

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

        # Mirror leave_channel: without this the moderation teardown skips the
        # call-lifecycle hook, so disconnecting the last member left the
        # channel's call active forever (duration growing, no transcription)
        # and a later rejoin was seen as an existing membership, bypassing the
        # recording-consent gate. Run it after the DELETEs so the channel
        # already reports the user as gone.
        self._on_member_left(target_user_id, state.channel_id)

        logger.debug(f"User {target_user_id} disconnected from voice by {moderator_id}")

        return True
