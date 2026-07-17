"""
Voice call lifecycle - glue between the voice state machine and the artifacts
``VoiceCallManager``.

A "call" is an aggregate over a voice channel: it starts when the first
participant joins a channel and ends when the last participant leaves. The
voice module has no native call object, so this mixin tracks active calls
lazily keyed by ``channel_id`` and persists them through ``VoiceCallManager``.

Voice must never break if the artifacts layer is unavailable: every interaction
with the call manager is guarded and logged, and a missing call manager simply
means no call records are produced.
"""

import utils.logger as logger
from typing import Optional

from ...base import SnowflakeID


from .protocol import VoiceProtocol


class CallLifecycleMixin(VoiceProtocol):
    def set_voice_call_manager(self, voice_call_manager) -> None:
        """Attach a ``VoiceCallManager`` (may be ``None`` to disable records)."""
        self._voice_call_manager = voice_call_manager

    def _get_voice_call_manager(self):
        manager = getattr(self, "_voice_call_manager", None)
        return manager

    def _resolve_conversation_for_channel(
        self, channel_id: SnowflakeID
    ) -> Optional[SnowflakeID]:
        """Best-effort lookup of a conversation id backing a channel.

        Voice channels are not always tied to a conversation; when one cannot be
        resolved we return ``None`` and the call is recorded without one.
        """
        try:
            row = self._db.fetch_one(
                "SELECT conversation_id FROM srv_channels WHERE id = ?",
                (channel_id,),
            )
            if row and row.get("conversation_id"):
                return row["conversation_id"]
        except Exception:
            return None
        return None

    def _on_member_joined(self, user_id: SnowflakeID, channel_id: SnowflakeID) -> None:
        manager = self._get_voice_call_manager()
        if manager is None:
            return
        try:
            existing = manager.get_active_by_channel(channel_id)
            if existing is None:
                state = self.get_voice_state(user_id)
                server_id = state.server_id if state else None
                conversation_id = self._resolve_conversation_for_channel(channel_id)
                call = manager.start_call(
                    channel_id=channel_id,
                    server_id=server_id,
                    initiator_id=user_id,
                    conversation_id=conversation_id,
                )
                logger.info(f"Started voice call {call.id} for channel {channel_id}")
        except Exception as exc:
            logger.warning(
                f"Failed to start voice call for channel {channel_id}: {exc}"
            )

    def _on_member_left(self, user_id: SnowflakeID, channel_id: SnowflakeID) -> None:
        manager = self._get_voice_call_manager()
        if manager is None:
            return
        try:
            existing = manager.get_active_by_channel(channel_id)
            if existing is None:
                return
            remaining = self.get_channel_members(channel_id)
            if not remaining:
                participant_ids = [user_id]
                call = manager.end_call(existing.id, participant_ids=participant_ids)
                logger.info(
                    f"Ended voice call {call.id} for channel {channel_id} "
                    f"({call.duration_seconds}s)"
                )
        except Exception as exc:
            logger.warning(f"Failed to end voice call for channel {channel_id}: {exc}")
