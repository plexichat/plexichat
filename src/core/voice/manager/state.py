from typing import Optional

from ...base import SnowflakeID
from ..models import VoiceState
from ..exceptions import UserNotInChannelError


from .protocol import VoiceProtocol


class StateMixin(VoiceProtocol):
    def get_voice_state(self, user_id: SnowflakeID) -> Optional[VoiceState]:
        row = self._db.fetch_one(
            "SELECT * FROM voice_states WHERE user_id = ?", (user_id,)
        )
        return self._row_to_voice_state(row) if row else None

    def set_self_mute(self, user_id: SnowflakeID, muted: bool) -> VoiceState:
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

            allowed_columns = {
                "channel_id",
                "guild_id",
                "session_id",
                "deaf",
                "mute",
                "self_deaf",
                "self_mute",
                "self_video",
                "suppress",
                "request_to_speak_timestamp",
                "streaming",
                "video",
                "last_activity",
            }
            for update in updates:
                col_name = update.split(" = ")[0]
                if col_name not in allowed_columns:
                    raise ValueError(f"Invalid column name: {col_name}")

            for update in updates:
                if "channel_id = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE voice_states SET channel_id = ?, last_activity = ? WHERE user_id = ?",
                        (params[idx], now, user_id),
                    )
                elif "guild_id = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE voice_states SET guild_id = ?, last_activity = ? WHERE user_id = ?",
                        (params[idx], now, user_id),
                    )
                elif "session_id = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE voice_states SET session_id = ?, last_activity = ? WHERE user_id = ?",
                        (params[idx], now, user_id),
                    )
                elif "deaf = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE voice_states SET deaf = ?, last_activity = ? WHERE user_id = ?",
                        (params[idx], now, user_id),
                    )
                elif "mute = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE voice_states SET mute = ?, last_activity = ? WHERE user_id = ?",
                        (params[idx], now, user_id),
                    )
                elif "self_deaf = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE voice_states SET self_deaf = ?, last_activity = ? WHERE user_id = ?",
                        (params[idx], now, user_id),
                    )
                elif "self_mute = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE voice_states SET self_mute = ?, last_activity = ? WHERE user_id = ?",
                        (params[idx], now, user_id),
                    )
                elif "self_video = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE voice_states SET self_video = ?, last_activity = ? WHERE user_id = ?",
                        (params[idx], now, user_id),
                    )
                elif "suppress = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE voice_states SET suppress = ?, last_activity = ? WHERE user_id = ?",
                        (params[idx], now, user_id),
                    )
                elif "request_to_speak_timestamp = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE voice_states SET request_to_speak_timestamp = ?, last_activity = ? WHERE user_id = ?",
                        (params[idx], now, user_id),
                    )
                elif "streaming = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE voice_states SET streaming = ?, last_activity = ? WHERE user_id = ?",
                        (params[idx], now, user_id),
                    )
                elif "video = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE voice_states SET video = ?, last_activity = ? WHERE user_id = ?",
                        (params[idx], now, user_id),
                    )

        result = self.get_voice_state(user_id)
        assert result is not None
        return result
