from typing import Optional, List

import utils.logger as logger
from ...base import SnowflakeID
from ..models import VoiceState, StageInstance, SpeakerRequest
from ..exceptions import (
    VoiceError,
    ChannelNotFoundError,
    ChannelTypeError,
    PermissionDeniedError,
    StageNotFoundError,
    SpeakerRequestNotFoundError,
    SpeakerRequestExistsError,
    AlreadySpeakerError,
    NotSpeakerError,
    UserNotInChannelError,
)


from .protocol import VoiceProtocol


class StageOpsMixin(VoiceProtocol):
    def start_stage(
        self, user_id: SnowflakeID, channel_id: SnowflakeID, topic: str
    ) -> StageInstance:
        channel = self._get_server_channel(channel_id)
        if not channel:
            raise ChannelNotFoundError(f"Channel {channel_id} not found")

        if channel["channel_type"] != "stage":
            raise ChannelTypeError(
                "Not a stage channel", expected="stage", actual=channel["channel_type"]
            )

        server_id = channel["server_id"]
        self._require_permission(user_id, server_id, "voice.speak", channel_id)

        state = self.get_voice_state(user_id)
        if not state or state.channel_id != channel_id:
            raise UserNotInChannelError(
                f"User {user_id} is not in channel {channel_id}"
            )

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
            self._invalidate_voice_state(user_id)

        logger.debug(f"Stage started in channel {channel_id} by user {user_id}")

        result = self.get_stage(channel_id)
        assert result is not None
        return result

    def end_stage(self, user_id: SnowflakeID, channel_id: SnowflakeID) -> bool:
        stage = self.get_stage(channel_id)
        if not stage:
            raise StageNotFoundError(f"No active stage in channel {channel_id}")

        state = self.get_voice_state(user_id)
        if not state or state.channel_id != channel_id:
            raise UserNotInChannelError(
                f"User {user_id} is not in channel {channel_id}"
            )

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
        # This UPDATE touches every member of the channel, not one known user.
        # Drop all cached states rather than try to enumerate affected ids.
        self._invalidate_all_lookups()

        self._db.execute(
            "DELETE FROM voice_stage_instances WHERE channel_id = ?", (channel_id,)
        )

        logger.debug(f"Stage ended in channel {channel_id} by user {user_id}")

        return True

    def get_stage(self, channel_id: SnowflakeID) -> Optional[StageInstance]:
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

        # SECURITY (member-target hierarchy): inviting someone to speak changes
        # that member's stage role, so the same role-hierarchy gate that guards
        # voice.mute_members/deafen_members/move_members in ModerationMixin must
        # apply here. Without it a moderator holding voice.mute_members could
        # promote the server owner or a peer ranked at or above them into a
        # speaker. Check before the state/suppress lookups so the denial is
        # deterministic regardless of the target's current audience state.
        self._require_can_moderate_member(
            moderator_id, target_user_id, server_id, "voice.mute_members"
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
        self._invalidate_voice_state(target_user_id)

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
            # SECURITY (member-target hierarchy): moving a speaker to the
            # audience strips their ability to broadcast, i.e. it is a
            # moderation action against another member. voice.mute_members
            # alone must not let a moderator strip the server owner or a peer
            # ranked at or above them. This mirrors the kick/ban and
            # mute/deafen/move/disconnect hierarchy gates. Self-demotion stays
            # a no-permission self-service action, so the check is skipped for
            # ``is_self``.
            self._require_can_moderate_member(
                moderator_id, target_user_id, server_id, "voice.mute_members"
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
        self._invalidate_voice_state(target_user_id)

        logger.debug(f"User {target_user_id} moved to audience by {moderator_id}")

        result = self.get_voice_state(target_user_id)
        assert result is not None
        return result

    def get_speaker_requests(self, channel_id: SnowflakeID) -> List[SpeakerRequest]:
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
        rows = self._db.fetch_all(
            "SELECT * FROM voice_states WHERE channel_id = ? AND suppress = 0",
            (channel_id,),
        )
        return [self._row_to_voice_state(row) for row in rows]

    def get_audience(self, channel_id: SnowflakeID) -> List[VoiceState]:
        rows = self._db.fetch_all(
            "SELECT * FROM voice_states WHERE channel_id = ? AND suppress = 1",
            (channel_id,),
        )
        return [self._row_to_voice_state(row) for row in rows]
