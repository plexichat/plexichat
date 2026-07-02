from typing import Optional, List

import utils.logger as logger
from ...base import SnowflakeID
from ..models import VoiceState, VoiceChannel
from ..exceptions import (
    ChannelNotFoundError,
    ChannelTypeError,
    ChannelFullError,
    UserNotInChannelError,
)


from .protocol import VoiceProtocol


class ChannelOpsMixin(VoiceProtocol):
    def join_channel(self, user_id: SnowflakeID, channel_id: SnowflakeID) -> VoiceState:
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
                return existing
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
        current = self.get_voice_state(user_id)
        if current:
            self.leave_channel(user_id)

        return self.join_channel(user_id, channel_id)

    def get_channel_users(self, channel_id: SnowflakeID) -> List[VoiceState]:
        rows = self._db.fetch_all(
            "SELECT * FROM voice_states WHERE channel_id = ?", (channel_id,)
        )
        return [self._row_to_voice_state(row) for row in rows]

    def get_voice_channel(
        self, channel_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[VoiceChannel]:
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
        rows = self._db.fetch_all(
            """SELECT * FROM srv_channels
               WHERE server_id = ? AND channel_type IN ('voice', 'stage') AND deleted = 0
               ORDER BY position""",
            (server_id,),
        )

        # Pre-filter rows the caller can view BEFORE asking the
        # database to compute per-channel counts. The bulk COUNT is
        # scoped to the channels we will return, so we don't pay for
        # rows that the permissions filter will drop anyway.
        visible_rows: list = []
        for row in rows:
            if self._servers and not self._servers.has_permission(
                user_id, server_id, "channels.view", row["id"]
            ):
                continue
            visible_rows.append(dict(row))
        if not visible_rows:
            return []

        # One bulk SELECT COUNT for ALL visible channels — replaces
        # the per-row COUNT that ``_row_to_voice_channel`` would
        # otherwise fire. Net: an N-channels list query is now exactly
        # 1 SELECT-listing + 1 SELECT-COUNT instead of N+1.
        prefetched_counts = self._get_channel_user_counts_bulk(  # type: ignore[attr-defined]  # mixed in via ops mixin
            [r["id"] for r in visible_rows]
        )

        channels: List[VoiceChannel] = []
        for row in visible_rows:
            settings = self._get_channel_settings(row["id"])
            channels.append(
                self._row_to_voice_channel(row, settings, prefetched_counts)  # type: ignore[call-arg]  # positional arg signed different from caller
            )

        return channels
