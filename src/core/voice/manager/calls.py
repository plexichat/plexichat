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
        manager = self._voice_call_manager
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
        manager = self._voice_call_manager
        if manager is None:
            return
        try:
            existing = manager.get_active_by_channel(channel_id)
            if existing is None:
                return
            remaining = self.get_channel_members(channel_id)
            if not remaining:
                try:
                    user_count = self._get_channel_user_count(channel_id)
                    if user_count > 0:
                        logger.warning(
                            f"get_channel_members returned 0 for channel {channel_id} "
                            f"but get_channel_user_count reports {user_count} — skipping end_call"
                        )
                        return
                except Exception as exc:
                    logger.debug(
                        f"get_channel_user_count failed for {channel_id}: {exc}"
                    )
                participant_ids = [user_id]
                call = manager.end_call(existing.id, participant_ids=participant_ids)
                logger.info(
                    f"Ended voice call {call.id} for channel {channel_id} "
                    f"({call.duration_seconds}s)"
                )
                self._maybe_schedule_transcription(call.id, channel_id)
        except Exception as exc:
            logger.warning(f"Failed to end voice call for channel {channel_id}: {exc}")

    def _maybe_schedule_transcription(
        self, call_id: SnowflakeID, channel_id: SnowflakeID
    ) -> None:
        """Fire-and-forget auto-transcription when enabled and licensed.

        Respects ``artifacts.voice.transcription.{enabled,auto_transcribe}`` and
        the ``voice_transcription`` capability. The scheduler never blocks the
        voice lifecycle path; failures are handled inside the worker.
        """
        try:
            from src.core.artifacts.transcription.worker import (
                schedule_transcribe_call,
            )

            transcription_cfg = None
            try:
                import utils.config as cfg

                artifacts_cfg = cfg.get("artifacts", {}) or {}
                transcription_cfg = (artifacts_cfg.get("voice", {}) or {}).get(
                    "transcription", {}
                ) or {}
            except Exception:
                transcription_cfg = None

            if transcription_cfg is None:
                return
            if not transcription_cfg.get("enabled", False):
                return
            if not transcription_cfg.get("auto_transcribe", False):
                return

            db = getattr(self, "_db", None)
            if db is None:
                return
            schedule_transcribe_call(call_id, db, transcription_cfg)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(f"transcription scheduling skipped: {exc}")
