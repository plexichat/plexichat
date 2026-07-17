"""
Transcription worker.

Turns a finished voice call into a transcript artifact. The worker is the single
place that:

1. Loads the ``voice_calls`` row and enforces consent (no transcript without
   participant consent when consent is required).
2. Resolves the recording reference (path/URL stored on the call payload).
3. Runs the configured :class:`TranscriptionProvider`.
4. Persists a ``TRANSCRIPT`` artifact linked to the call, flags the call and its
   ``voice_call`` artifact, and links the transcript via
   :meth:`VoiceCallManager.set_transcript`.
5. Emits an ``ARTIFACT_UPDATE`` so clients refresh.

The worker never raises to its caller: transcription failures are logged and the
call is left without a transcript. Scheduling is fire-and-forget via
:func:`schedule_transcribe_call` (``asyncio.create_task`` against the running
loop, with a bounded in-process queue fallback for environments without a live
loop at schedule time).
"""

import asyncio
from typing import Any, Dict, List, Optional

import utils.config as config
import utils.logger as logger

from src.core.base import SnowflakeID
from src.core.artifacts.models import ArtifactType, ArtifactStatus
from src.core.artifacts.repository import (
    get_voice_call,
    get_artifact,
)
from src.core.artifacts.voice_calls import VoiceCallManager
from src.core.artifacts.manager import ArtifactManager
from src.core.artifacts.capabilities import (
    get_capability,
    CapabilityState,
)
from src.core.artifacts.transcription.provider import (
    get_transcription_provider,
    TranscriptionResult,
)


def _transcription_config() -> Dict[str, Any]:
    artifacts_cfg = config.get("artifacts", {}) or {}
    voice_cfg = artifacts_cfg.get("voice", {}) or {}
    transcription_cfg = voice_cfg.get("transcription", {}) or {}
    if not isinstance(transcription_cfg, dict):
        transcription_cfg = {}
    return transcription_cfg


def _capability_available() -> bool:
    """Return True only when voice_transcription is fully AVAILABLE."""
    info = get_capability("voice_transcription")
    return info.state == CapabilityState.AVAILABLE


def _resolve_recording_ref(call_payload: Dict[str, Any]) -> Optional[str]:
    """Pull a recording reference from the call payload.

    Recordings are stored as a reference (absolute path or media URL) under
    ``recording_ref``. When absent we cannot transcribe.
    """
    if not isinstance(call_payload, dict):
        return None
    ref = call_payload.get("recording_ref")
    if isinstance(ref, str) and ref:
        return ref
    return None


def _consent_allows(call: Any) -> bool:
    """Decide whether consent permits transcription of this call.

    Consent is required unless the server config opts out (``consent_required``
    is explicitly ``False``). When required, at least one consented participant
    must be recorded.
    """
    transcription_cfg = _transcription_config()
    consent_required = transcription_cfg.get("consent_required", True)
    if consent_required is False:
        return True
    consented = getattr(call, "consented_participants", None) or []
    return len(consented) > 0


def _emit_artifact_update(
    transcript_artifact: Any,
    conversation_id: Optional[SnowflakeID],
    server_id: Optional[SnowflakeID],
    channel_id: Optional[SnowflakeID],
) -> None:
    """Emit an ARTIFACT_UPDATE so clients refresh the artifact pane."""
    try:
        from src.core import events

        if not events.is_setup():
            return

        event = events.Event(
            event_type=events.EventType.ARTIFACT_UPDATE,
            data={
                "artifact_id": str(transcript_artifact.id),
                "artifact_type": transcript_artifact.artifact_type.value,
                "has_transcript": True,
                "call_id": str(transcript_artifact.payload.get("voice_call_id", "")),
            },
            server_id=int(server_id) if server_id else None,
            channel_id=int(channel_id) if channel_id else None,
        )

        user_ids: Optional[List[int]] = None
        try:
            import src.api as api

            messaging_mod = api.get_messaging()
            if conversation_id is not None and messaging_mod is not None:
                user_ids = [
                    int(u) for u in messaging_mod.get_participant_ids(conversation_id)
                ]
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(f"transcription: participant resolve failed: {exc}")
            user_ids = None

        events.dispatch(
            event,
            user_ids=user_ids,
            server_id=int(server_id) if server_id else None,
            channel_id=int(channel_id) if channel_id else None,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"transcription: failed to emit ARTIFACT_UPDATE: {exc}")


async def transcribe_call(
    call_id: SnowflakeID, db: Any, config_arg: Any = None
) -> Optional[int]:
    """Transcribe a finished call and persist the transcript artifact.

    Returns the created transcript artifact id, or ``None`` when transcription
    is skipped (capability off, no recording, missing consent) or failed.
    """
    transcription_cfg = (
        config_arg if config_arg is not None else _transcription_config()
    )

    try:
        # Gate on capability state (single source of truth for DEPENDENCY_MISSING
        # / misconfigured / disabled). When not AVAILABLE we no-op with a log.
        if not _capability_available():
            logger.info(
                f"transcription: skipping call {call_id}; capability not AVAILABLE."
            )
            return None

        enabled = transcription_cfg.get("enabled", False)
        auto = transcription_cfg.get("auto_transcribe", False)
        if not enabled or not auto:
            logger.info(
                f"transcription: skipping call {call_id}; "
                f"enabled={enabled} auto_transcribe={auto}."
            )
            return None

        call = get_voice_call(db, call_id)
        if call is None:
            logger.warning(f"transcription: call {call_id} not found.")
            return None

        if not call.recorded:
            logger.info(f"transcription: call {call_id} was not recorded; skip.")
            return None

        if not _consent_allows(call):
            logger.info(f"transcription: call {call_id} lacks consent; skip (GDPR).")
            return None

        artifact = None
        if call.artifact_id is not None:
            artifact = get_artifact(db, call.artifact_id)
        call_payload: Dict[str, Any] = {}
        if artifact is not None and isinstance(artifact.payload, dict):
            call_payload = artifact.payload
        # The voice_calls row also carries a payload shadow; the manager stores it
        # on the linked artifact, so prefer that.

        recording_ref = _resolve_recording_ref(call_payload)
        if recording_ref is None:
            logger.warning(f"transcription: call {call_id} has no recording_ref; skip.")
            return None

        provider = get_transcription_provider(transcription_cfg)
        if not provider.is_available():
            logger.warning(
                f"transcription: provider unavailable for call {call_id}; skip."
            )
            return None

        opts: Dict[str, Any] = {
            "language": transcription_cfg.get("language", "auto"),
            "diarize": transcription_cfg.get("diarize", False),
        }
        logger.info(f"transcription: running provider for call {call_id}...")
        result: TranscriptionResult = await provider.transcribe(recording_ref, opts)

        artifact_manager = ArtifactManager(db, config.get("artifacts", {}) or {})
        voice_manager = VoiceCallManager(
            db,
            artifact_manager=artifact_manager,
            config=config.get("artifacts", {}) or {},
        )

        transcript_artifact = artifact_manager.create(
            conversation_id=call.conversation_id,
            author_id=call.initiator_id if call.initiator_id else 0,
            artifact_type=ArtifactType.TRANSCRIPT,
            title=f"Transcript of call {call.id}",
            summary=f"Auto-generated transcript ({result.language})",
            channel_id=call.channel_id,
            server_id=call.server_id,
            status=ArtifactStatus.COMPLETED,
            recorded=True,
            has_transcript=False,
            payload={
                "voice_call_id": call.id,
                "segments": result.segments,
                "language": result.language,
                "text": result.text,
            },
        )

        voice_manager.set_transcript(call.id, transcript_artifact.id)

        _emit_artifact_update(
            transcript_artifact,
            call.conversation_id,
            call.server_id,
            call.channel_id,
        )

        logger.info(
            f"transcription: call {call_id} -> transcript "
            f"{transcript_artifact.id} ({len(result.segments)} segments)."
        )
        return int(transcript_artifact.id)
    except Exception as exc:
        logger.error(f"transcription: failed for call {call_id}: {exc}", exc_info=True)
        return None


# === Fire-and-forget scheduling ===

# Bounded in-process queue used when no asyncio loop is running at schedule time
# (e.g. a synchronous lifecycle hook). A background task drains it once a loop
# starts; this keeps the API non-blocking in both sync and async contexts.
_QUEUE: "asyncio.Queue" = asyncio.Queue()
_QUEUE_DRAINING = False


async def _drain_queue() -> None:
    """Process any queued transcription jobs. Runs while the loop is alive."""
    global _QUEUE_DRAINING
    _QUEUE_DRAINING = True
    try:
        while True:
            job = await _QUEUE.get()
            try:
                await transcribe_call(job["call_id"], job["db"], job["config"])
            except Exception as exc:  # noqa: BLE001 - never crash the drainer
                logger.error(f"transcription: queued job failed: {exc}")
            finally:
                _QUEUE.task_done()
    except asyncio.CancelledError:  # pragma: no cover - shutdown
        return
    finally:
        _QUEUE_DRAINING = False


def schedule_transcribe_call(
    call_id: SnowflakeID, db: Any, config_arg: Any = None
) -> None:
    """Schedule :func:`transcribe_call` without blocking the caller.

    Uses ``asyncio.create_task`` when a loop is running; otherwise enqueues the
    job on the in-process queue so a later ``ensure_drainer`` call (wired into
    the app startup) can process it.
    """
    transcription_cfg = (
        config_arg if config_arg is not None else _transcription_config()
    )
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            loop.create_task(transcribe_call(call_id, db, transcription_cfg))
            return
    except RuntimeError:
        pass

    # No running loop: defer to the bounded queue.
    _QUEUE.put_nowait({"call_id": call_id, "db": db, "config": transcription_cfg})


def ensure_transcription_drainer() -> None:
    """Start the queue drainer task if a loop is running and not yet started."""
    global _QUEUE_DRAINING
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if loop.is_running() and not _QUEUE_DRAINING:
        loop.create_task(_drain_queue())


__all__ = [
    "transcribe_call",
    "schedule_transcribe_call",
    "ensure_transcription_drainer",
]
