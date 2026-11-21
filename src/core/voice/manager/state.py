from typing import Any, Dict, Optional

from ...base import SnowflakeID
from ..models import VoiceState
from ..exceptions import UserNotInChannelError


from .cache import _MISS
from .protocol import VoiceProtocol


class StateMixin(VoiceProtocol):
    def _load_voice_state(self, user_id: SnowflakeID) -> Optional[VoiceState]:
        """Authoritative, uncached read of a user's voice state row."""
        row = self._db.fetch_one(
            "SELECT * FROM voice_states WHERE user_id = ?", (user_id,)
        )
        return self._row_to_voice_state(row) if row else None

    def get_voice_state(self, user_id: SnowflakeID) -> Optional[VoiceState]:
        """Return a user's voice state, memoized for a short bounded window.

        ``VOICE_SPEAKING`` frames call this on every frame for the active
        speaker; the state is read-only there, so the cache collapses a burst
        into a single query.  All state mutations invalidate the user's entry
        before their read-after-write, so a write never observes its own stale
        value.  Returned instances must be treated as immutable (current
        callers only read fields).
        """
        cache = self._get_voice_lookup_cache()
        key = cache.state_key(user_id)
        cached = cache.get(key)
        if cached is not _MISS:
            return cached  # VoiceState is treated as immutable by all callers
        state = self._load_voice_state(user_id)
        cache.set(key, state)
        return state

    def set_self_mute(self, user_id: SnowflakeID, muted: bool) -> VoiceState:
        state = self.get_voice_state(user_id)
        if not state:
            raise UserNotInChannelError(f"User {user_id} is not in a voice channel")

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE voice_states SET self_mute = ?, last_activity = ? WHERE user_id = ?",
            (1 if muted else 0, now, user_id),
        )
        self._invalidate_voice_state(user_id)

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
        self._invalidate_voice_state(user_id)

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
        self._invalidate_voice_state(user_id)

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
        self._invalidate_voice_state(user_id)

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

        if self_deaf is not None:
            updates.append("self_deaf = ?")
            params.append(1 if self_deaf else 0)
            if self_deaf:
                updates.append("self_mute = ?")
                params.append(1)

        # Client-supplied state spoofing: VOICE_STATE_UPDATE carries both
        # toggles, so a client can send self_deaf=true, self_mute=false in a
        # single frame. The deafen block above implicitly mutes, but a later
        # explicit self_mute=false would overwrite that and leave the user
        # broadcast as deafened while still unmuted (peers are told the user
        # cannot hear them, yet their audio is live). Enforce the same
        # deafen=>mute invariant the single-field set_self_deaf path does:
        # while deafening, the implicit mute wins over any explicit unmute.
        if self_mute is not None and self_deaf is not True:
            updates.append("self_mute = ?")
            params.append(1 if self_mute else 0)

        if streaming is not None:
            updates.append("streaming = ?")
            params.append(1 if streaming else 0)

        if video is not None:
            updates.append("video = ?")
            params.append(1 if video else 0)

        if updates:
            now = self._get_timestamp()
            allowed_columns = {
                "self_deaf",
                "self_mute",
                "suppress",
                "streaming",
                "video",
                "last_activity",
            }

            # BEFORE: one UPDATE per requested field (up to five), each writing
            # ``last_activity`` and re-entering the DB, so a client that sent
            # mute+deaf+stream+video paid five writes for one state change.
            # AFTER: a single UPDATE covering every requested field.
            #
            # A repeated column (self_deaf=True forces mute, then an explicit
            # self_mute) collapses to its last value: that is exactly the final
            # state the old sequential statements produced, without depending
            # on dialect-specific duplicate-assignment behaviour. The column
            # allowlist still gates every interpolated identifier, the write is
            # still scoped to the caller's user_id, and one atomic statement is
            # strictly safer across replicas than a burst of partial updates.
            assignments: Dict[str, Any] = {}
            for update, value in zip(updates, params):
                col_name = update.split(" = ")[0]
                if col_name not in allowed_columns:
                    raise ValueError(f"Invalid column name: {col_name}")
                if col_name == "last_activity":
                    continue
                assignments[col_name] = value

            set_clause = ", ".join(f"{col} = ?" for col in assignments)
            self._db.execute(
                f"UPDATE voice_states SET {set_clause}, last_activity = ? "
                "WHERE user_id = ?",
                (*assignments.values(), now, user_id),
            )

            self._invalidate_voice_state(user_id)

        result = self.get_voice_state(user_id)
        assert result is not None
        return result
