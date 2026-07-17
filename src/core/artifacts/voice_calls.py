"""
Voice call artifact manager - call lifecycle for the artifacts feature.

Wraps the repository (DB access) and the artifacts config to provide a clean
facade for the lifecycle of a voice call: starting a call (which also creates a
linked ``voice_call`` Artifact in LIVE status), ending a call (which completes
the linked artifact), toggling recording, recording consent, and attaching a
transcript artifact (produced by a later transcription group).

The manager is deliberately tolerant of a missing/unavailable artifacts layer:
every artifact interaction is wrapped so that a failure there never breaks the
voice pipeline (voice must keep working even when the artifacts feature is off).
"""

import utils.logger as logger
import utils.config as config
from typing import Any, Dict, List, Optional

from src.core.base import BaseManager, SnowflakeID
from .models import (
    ArtifactType,
    ArtifactStatus,
    VoiceCall,
)
from .manager import ArtifactManager
from .repository import (
    create_voice_call,
    get_voice_call,
    get_active_voice_call_by_channel,
    update_voice_call,
)


class VoiceCallManager(BaseManager):
    """Manager for voice-call domain logic and linked artifacts."""

    def __init__(
        self,
        db,
        artifact_manager: Optional["ArtifactManager"] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Initialize the voice call manager.

        Args:
            db: Database instance (must be connected).
            artifact_manager: Optional ArtifactManager used to create/update the
                linked ``voice_call`` artifact. When ``None`` no artifact is
                produced (voice still works, just without the history record).
            config: Optional artifacts config dict. When omitted the config is
                loaded via ``utils.config.get("artifacts", {})``.
        """
        super().__init__(db, None)
        self._artifact_manager = artifact_manager
        self._artifacts_config = config if config is not None else {}

    # === Config helpers ===

    def _voice_config(self) -> Dict[str, Any]:
        artifacts_cfg = self._artifacts_config or {}
        if not artifacts_cfg:
            artifacts_cfg = config.get("artifacts", {}) or {}
        voice_cfg = artifacts_cfg.get("voice") or {}
        if not isinstance(voice_cfg, dict):
            voice_cfg = {}
        return voice_cfg

    def _allow_recording(self) -> bool:
        return bool(self._voice_config().get("allow_recording", True))

    # === Lifecycle ===

    def start_call(
        self,
        channel_id: SnowflakeID,
        server_id: SnowflakeID,
        initiator_id: SnowflakeID,
        conversation_id: Optional[SnowflakeID] = None,
    ) -> VoiceCall:
        """Begin a voice call and create its linked LIVE artifact.

        Inserts a ``voice_calls`` row (started_at now, participant_count 1) and,
        when an artifact manager is present, a corresponding ``voice_call``
        Artifact in LIVE status. The ``voice_calls.id`` is stored in the
        artifact payload so the two records can be cross-referenced.
        """
        now = self._get_timestamp()
        call_id = self._generate_id()

        artifact_id: Optional[SnowflakeID] = None
        if self._artifact_manager is not None:
            try:
                recorded = self._allow_recording()
                title = "Voice call"
                artifact = self._artifact_manager.create(
                    conversation_id=conversation_id,
                    author_id=initiator_id,
                    artifact_type=ArtifactType.VOICE_CALL,
                    title=title,
                    summary="Voice call in progress",
                    channel_id=channel_id,
                    server_id=server_id,
                    status=ArtifactStatus.LIVE,
                    recorded=recorded,
                    has_transcript=False,
                    payload={
                        "voice_call_id": call_id,
                        "channel_id": channel_id,
                        "participant_count": 1,
                    },
                )
                artifact_id = artifact.id
            except Exception as exc:
                logger.warning(
                    f"voice_call artifact creation failed (call {call_id}): {exc}"
                )
                artifact_id = None

        call = VoiceCall(
            id=call_id,
            conversation_id=conversation_id,
            channel_id=channel_id,
            server_id=server_id,
            initiator_id=initiator_id,
            started_at=now,
            created_at=now,
            updated_at=now,
            artifact_id=artifact_id,
            participant_count=1,
            recorded=bool(artifact_id is not None and self._allow_recording()),
        )
        return create_voice_call(self._db, call)

    def end_call(
        self,
        call_id: SnowflakeID,
        participant_ids: Optional[List[SnowflakeID]] = None,
    ) -> VoiceCall:
        """End a voice call and complete its linked artifact.

        Sets ``ended_at``, ``duration_seconds``, and ``participant_count`` on the
        ``voice_calls`` row, then transitions the linked artifact (if any) to
        COMPLETED status. Returns the refreshed row.
        """
        call = get_voice_call(self._db, call_id)
        if call is None:
            raise ValueError(f"Voice call {call_id} not found")

        ended_at = self._get_timestamp()
        duration = max(0, (ended_at - call.started_at) // 1000)
        participant_count = (
            len(participant_ids) if participant_ids else call.participant_count
        )

        updated = update_voice_call(
            self._db,
            call_id,
            ended_at=ended_at,
            duration_seconds=duration,
            participant_count=participant_count,
            updated_at=ended_at,
        )
        assert updated is not None

        if self._artifact_manager is not None and updated.artifact_id is not None:
            try:
                self._artifact_manager.update(
                    updated.artifact_id,
                    status=ArtifactStatus.COMPLETED,
                    updated_at=ended_at,
                    payload={
                        "voice_call_id": updated.id,
                        "channel_id": updated.channel_id,
                        "participant_count": participant_count,
                        "duration_seconds": duration,
                    },
                )
            except Exception as exc:
                logger.warning(
                    f"voice_call artifact completion failed (call {call_id}): {exc}"
                )

        return updated

    def mark_recorded(self, call_id: SnowflakeID, recorded: bool) -> VoiceCall:
        """Update the recorded flag on both the call and its linked artifact."""
        if recorded and not self._allow_recording():
            recorded = False

        updated = update_voice_call(self._db, call_id, recorded=recorded)
        if updated is None:
            raise ValueError(f"Voice call {call_id} not found")

        if self._artifact_manager is not None and updated.artifact_id is not None:
            try:
                self._artifact_manager.update(updated.artifact_id, recorded=recorded)
            except Exception as exc:
                logger.warning(
                    f"voice_call artifact record-flag update failed (call {call_id}): {exc}"
                )

        return updated

    def add_consent(self, call_id: SnowflakeID, user_id: SnowflakeID) -> VoiceCall:
        """Append a user to the consented-participants list (deduped)."""
        call = get_voice_call(self._db, call_id)
        if call is None:
            raise ValueError(f"Voice call {call_id} not found")

        consented = list(call.consented_participants)
        if user_id not in consented:
            consented.append(user_id)

        updated = update_voice_call(
            self._db,
            call_id,
            consented_participants=consented,
            updated_at=self._get_timestamp(),
        )
        assert updated is not None
        return updated

    def set_transcript(
        self,
        call_id: SnowflakeID,
        transcript_artifact_id: SnowflakeID,
    ) -> VoiceCall:
        """Link a transcript artifact to the call and flag the artifact."""
        updated = update_voice_call(
            self._db,
            call_id,
            transcript_artifact_id=transcript_artifact_id,
            updated_at=self._get_timestamp(),
        )
        if updated is None:
            raise ValueError(f"Voice call {call_id} not found")

        if self._artifact_manager is not None and updated.artifact_id is not None:
            try:
                self._artifact_manager.update(
                    updated.artifact_id,
                    has_transcript=True,
                    updated_at=self._get_timestamp(),
                )
            except Exception as exc:
                logger.warning(
                    f"voice_call artifact transcript link failed (call {call_id}): {exc}"
                )

        return updated

    def get_active_by_channel(self, channel_id: SnowflakeID) -> Optional[VoiceCall]:
        """Return the active (not yet ended) call for a channel, if any."""
        return get_active_voice_call_by_channel(self._db, channel_id)
