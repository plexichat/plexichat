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
from .capabilities import CapabilityState, get_capability
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
        self._recording_manager: Any = None
        # Live participant identities for active calls. This is deliberately
        # process-local: voice_states remains the authoritative membership
        # store, while this snapshot lets duplicate join callbacks be
        # distinguished from a real join after someone has departed.
        self._voice_call_participants: Dict[int, set[int]] = {}

    def set_recording_manager(self, recording_manager: Any) -> None:
        """Attach a RecordingManager (may be ``None`` to disable recording)."""
        self._recording_manager = recording_manager

    def remove_participant(self, call_id: SnowflakeID, user_id: SnowflakeID) -> None:
        """Forget a departed user from the active-call membership snapshot.

        ``voice_states`` remains authoritative for current membership. This
        process-local snapshot only records identities seen by join callbacks,
        so a later rejoin can correctly require fresh recording consent.
        """
        participants = self._voice_call_participants.get(int(call_id))
        if participants is not None:
            participants.discard(int(user_id))

    # === Config helpers ===

    def _voice_config(self) -> Dict[str, Any]:
        artifacts_cfg = (
            self._artifacts_config
            if self._artifacts_config
            else config.get("artifacts", {}) or {}
        )
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
        artifacts_capability = get_capability(
            "artifacts", self._artifacts_config or config.get("artifacts", {}) or {}
        )
        if (
            self._artifact_manager is not None
            and artifacts_capability.state == CapabilityState.AVAILABLE
        ):
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

        recorded = bool(artifact_id is not None and self._allow_recording())

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
            recorded=recorded,
        )
        result = create_voice_call(self._db, call)
        self._voice_call_participants[int(call_id)] = self._current_participant_ids(
            channel_id
        )

        # Fire-and-forget: request recording via RecordingManager.  When
        # consent is required the manager intentionally keeps this pending
        # until add_consent() confirms every known participant.
        if recorded and self._recording_manager is not None:
            self._recording_manager.start_call_recording(
                call_id=call_id,
                channel_id=channel_id,
                artifact_id=artifact_id,
                initiator_id=initiator_id,
            )

        return result

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
        if updated is None:
            raise ValueError(f"Failed to update voice call {call_id}")

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

        # Fire-and-forget: stop recording via RecordingManager
        if updated.recorded and self._recording_manager is not None:
            self._recording_manager.stop_call_recording(call_id)
        self._voice_call_participants.pop(int(call_id), None)

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

    @staticmethod
    def _require_channel_id(call: VoiceCall) -> SnowflakeID:
        """Return the channel ID required for live membership checks."""
        if call.channel_id is None:
            raise ValueError(f"Voice call {call.id} has no channel ID")
        return call.channel_id

    def _current_participant_ids(self, channel_id: SnowflakeID) -> set[int]:
        """Return the users currently present in the voice channel.

        ``voice_states`` is the authoritative live-membership source. The
        historical ``participant_count`` on ``voice_calls`` is intentionally
        retained for call analytics and must not be used as a consent roster.
        """
        try:
            rows = self._db.fetch_all(
                "SELECT user_id FROM voice_states WHERE channel_id = ?",
                (channel_id,),
            )
            return {int(row["user_id"]) for row in rows or []}
        except Exception as exc:
            logger.warning(
                "voice_call participant lookup failed for channel %s: %s",
                channel_id,
                exc,
            )
            return set()

    def add_participant(self, call_id: SnowflakeID, user_id: SnowflakeID) -> VoiceCall:
        """Record that another participant joined an active call.

        The persisted count remains a historical call metric. Consent is
        checked against the live ``voice_states`` membership instead, so a
        departed participant no longer blocks a later recording segment.
        """
        call = get_voice_call(self._db, call_id)
        if call is None:
            raise ValueError(f"Voice call {call_id} not found")
        normalized_user_id = int(user_id)
        current_ids = self._current_participant_ids(self._require_channel_id(call))
        if normalized_user_id not in current_ids:
            raise ValueError(
                f"User {normalized_user_id} is not currently in voice channel {call.channel_id}"
            )
        previous_ids = self._voice_call_participants.get(int(call_id))
        if previous_ids is None:
            # A manager reconstructed after the call began cannot know the
            # prior snapshot. Treat this callback conservatively as a join so
            # recording cannot continue without a fresh consent check.
            previous_ids = current_ids - {normalized_user_id}
        else:
            # Drop departed users before comparing: a later rejoin must count
            # as a new membership even when participant_count is historical.
            previous_ids = previous_ids & current_ids
        is_new_membership = normalized_user_id not in previous_ids
        self._voice_call_participants[int(call_id)] = set(current_ids)
        # The callback can be delivered more than once for the same live
        # membership. With no participant identity roster in the existing
        # schema, the live set gives us the safe idempotence boundary: never
        # count fewer than the number of distinct current members.
        previous_participant_count = int(call.participant_count or 0)
        participant_count = max(
            previous_participant_count,
            len(current_ids),
            1,
        )
        updated = update_voice_call(
            self._db,
            call_id,
            participant_count=participant_count,
            updated_at=self._get_timestamp(),
        )
        if updated is None:
            raise ValueError(f"Failed to update voice call {call_id}")

        # A newly joined participant has not consented yet.  End the current
        # recording segment immediately; a later consent event may start a new
        # segment once every currently known participant has agreed.
        current_ids = self._current_participant_ids(self._require_channel_id(updated))
        consented_ids = {int(value) for value in updated.consented_participants}
        if (
            is_new_membership
            and updated.recorded
            and self._recording_manager is not None
            and not current_ids.issubset(consented_ids)
        ):
            self._recording_manager.stop_call_recording(updated.id)
        return updated

    def add_consent(self, call_id: SnowflakeID, user_id: SnowflakeID) -> VoiceCall:
        """Append consent and start a pending recording once all consent."""
        call = get_voice_call(self._db, call_id)
        if call is None:
            raise ValueError(f"Voice call {call_id} not found")

        normalized_user_id = int(user_id)
        current_ids = self._current_participant_ids(self._require_channel_id(call))
        if normalized_user_id not in current_ids:
            raise ValueError(
                f"User {normalized_user_id} is not currently in voice channel {call.channel_id}"
            )

        consented = list(call.consented_participants)
        before_consented_ids = {int(value) for value in consented}
        if normalized_user_id not in before_consented_ids:
            consented.append(normalized_user_id)

        updated = update_voice_call(
            self._db,
            call_id,
            consented_participants=consented,
            updated_at=self._get_timestamp(),
        )
        if updated is None:
            raise ValueError(f"Failed to update voice call {call_id}")

        # Start when consent transitions from incomplete to complete. A
        # duplicate consent must be idempotent and must not restart a live
        # recording segment.
        current_ids = self._current_participant_ids(self._require_channel_id(updated))
        after_consented_ids = {int(value) for value in updated.consented_participants}
        was_complete = current_ids.issubset(before_consented_ids)
        is_complete = current_ids.issubset(after_consented_ids)
        if (
            updated.recorded
            and self._recording_manager is not None
            and is_complete
            and (not was_complete or normalized_user_id not in before_consented_ids)
        ):
            self._recording_manager.start_call_recording(
                call_id=updated.id,
                channel_id=updated.channel_id,
                artifact_id=updated.artifact_id,
                initiator_id=updated.initiator_id,
                force_restart=not was_complete,
            )
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
