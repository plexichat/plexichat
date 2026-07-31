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
import os
import shutil
import tempfile
import threading
from typing import Any, BinaryIO, Dict, List, Optional, Tuple, cast
from urllib.parse import urljoin, urlparse

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


def _public_https_origin() -> Optional[str]:
    """Return the configured public HTTPS origin used by remote providers."""
    import os as _os

    origin = _os.environ.get("PLEXICHAT_PUBLIC_SERVER_URL")
    if not origin:
        try:
            static_cfg = config.get("static_client", {}) or {}
            injection = static_cfg.get("config_injection", {}) or {}
            origin = injection.get("public_server_url")
        except Exception:
            origin = None
    if not origin:
        return None
    parsed = urlparse(str(origin).strip())
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        return None
    return f"https://{parsed.netloc}"


def _resolve_azure_recording_ref(
    call_payload: Dict[str, Any],
) -> Optional[str]:
    """Resolve a call recording to an HTTPS URL Azure can fetch server-side."""
    ref = call_payload.get("recording_ref")
    if isinstance(ref, str) and ref:
        parsed = urlparse(ref)
        if parsed.scheme.lower() == "https" and parsed.netloc:
            return ref

    file_id = call_payload.get("recording_file_id")
    origin = _public_https_origin()
    if not file_id or origin is None:
        logger.warning(
            "transcription: Azure requires a public HTTPS recording URL; "
            "no usable URL is configured."
        )
        return None

    try:
        import src.api as api

        media = api.get_media()
        if media is None or not hasattr(media, "sign_url"):
            return None
        signed = media.sign_url(int(file_id))
        signed_url = getattr(signed, "url", None)
        if not isinstance(signed_url, str) or not signed_url:
            return None
        parsed = urlparse(signed_url)
        if parsed.scheme.lower() == "https" and parsed.netloc:
            return signed_url
        return urljoin(origin + "/", signed_url.lstrip("/"))
    except Exception as exc:
        logger.warning(
            "transcription: could not create Azure recording URL for file %s: %s",
            file_id,
            exc,
        )
        return None


def _resolve_recording_ref(
    call_payload: Dict[str, Any],
    db: Any,
    provider_name: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Resolve a recording reference into the form a provider can consume.

    ``RecordingManager`` stores both the media file id and the relative API URL.
    Local Whisper and OpenAI require a local filesystem path, so materialize the
    media object into a temporary file for those providers. Azure requires a
    public HTTPS URL, so relative references are converted to signed URLs.
    The returned second value is a temporary path to remove after transcription.
    """
    del db  # retained in the private signature for compatibility with callers
    if not isinstance(call_payload, dict):
        return None, None
    ref = call_payload.get("recording_ref")
    if not isinstance(ref, str) or not ref:
        return None, None

    if provider_name == "azure":
        return _resolve_azure_recording_ref(call_payload), None
    if os.path.isfile(ref):
        return ref, None

    file_id = call_payload.get("recording_file_id")
    if not file_id:
        # Local Whisper/OpenAI cannot consume a relative API URL. Do not
        # fail open into a provider error; the worker will skip this call.
        return None, None

    temporary_path: Optional[str] = None
    try:
        import src.api as api

        media = api.get_media()
        if media is None:
            return None, None
        suffix = ".bin"
        stream = None
        content_type = None
        try:
            get_stream = getattr(media, "get_file_stream", None)
            if callable(get_stream):
                raw_stream_result = get_stream(int(file_id))
                if (
                    not isinstance(raw_stream_result, (tuple, list))
                    or len(raw_stream_result) != 3
                ):
                    raise TypeError("media.get_file_stream returned an invalid result")
                stream_result = cast(Tuple[Any, Any, Any], raw_stream_result)
                stream, _size, content_type = stream_result
                if not hasattr(stream, "read"):
                    stream = None
        except Exception:
            stream = None

        if stream is not None:
            stream = cast(BinaryIO, stream)
            try:
                if isinstance(content_type, str) and "/" in content_type:
                    suffix = "." + content_type.rsplit("/", 1)[-1].split("+", 1)[0]
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
                    temporary_path = handle.name
                    try:
                        shutil.copyfileobj(stream, handle)
                    except Exception:
                        try:
                            os.unlink(temporary_path)
                        except OSError:
                            pass
                        temporary_path = None
                        raise
                    return handle.name, handle.name
            finally:
                close = getattr(stream, "close", None)
                if callable(close):
                    close()

        get_data = getattr(media, "get_file_data", None)
        if not callable(get_data):
            return None, None
        raw_data_result = get_data(int(file_id))
        if not isinstance(raw_data_result, (tuple, list)) or len(raw_data_result) != 2:
            raise TypeError("media.get_file_data returned an invalid result")
        data_result = cast(Tuple[Any, Any], raw_data_result)
        data, content_type = data_result
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("media.get_file_data returned non-binary data")
        if isinstance(content_type, str) and "/" in content_type:
            suffix = "." + content_type.rsplit("/", 1)[-1].split("+", 1)[0]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            temporary_path = handle.name
            try:
                handle.write(bytes(data))
            except Exception:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass
                temporary_path = None
                raise
            return handle.name, handle.name
    except Exception as exc:
        logger.warning(
            "transcription: could not materialize recording file %s: %s",
            file_id,
            exc,
        )
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
        return None, None


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

        provider = get_transcription_provider(transcription_cfg)
        if not provider.is_available():
            logger.warning(
                f"transcription: provider unavailable for call {call_id}; skip."
            )
            return None

        recording_ref, temporary_ref = _resolve_recording_ref(
            call_payload,
            db,
            str(transcription_cfg.get("provider", "local_whisper")),
        )
        if recording_ref is None:
            logger.warning(f"transcription: call {call_id} has no recording_ref; skip.")
            return None

        opts: Dict[str, Any] = {
            "language": transcription_cfg.get("language", "auto"),
            "diarize": transcription_cfg.get("diarize", False),
        }
        logger.info(f"transcription: running provider for call {call_id}...")
        try:
            result: TranscriptionResult = await provider.transcribe(recording_ref, opts)
        finally:
            if temporary_ref:
                try:
                    os.unlink(temporary_ref)
                except OSError:
                    pass

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
_QUEUE = None
_QUEUE_DRAINING = False
_QUEUE_LOCK = threading.Lock()


def _get_queue():
    global _QUEUE
    if _QUEUE is None:
        _QUEUE = asyncio.Queue()
    return _QUEUE


async def _drain_queue() -> None:
    """Process any queued transcription jobs. Runs while the loop is alive."""
    try:
        while True:
            job = await _get_queue().get()
            try:
                await transcribe_call(job["call_id"], job["db"], job["config"])
            except Exception as exc:  # noqa: BLE001 - never crash the drainer
                logger.error(f"transcription: queued job failed: {exc}")
            finally:
                _get_queue().task_done()
    except asyncio.CancelledError:  # pragma: no cover - shutdown
        return
    finally:
        with _QUEUE_LOCK:
            global _QUEUE_DRAINING
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
        logger.debug("No running loop; enqueuing transcription job")
        pass

    # No running loop: defer to the bounded queue.
    _get_queue().put_nowait({"call_id": call_id, "db": db, "config": transcription_cfg})
    ensure_transcription_drainer()


def ensure_transcription_drainer() -> None:
    """Start the queue drainer task if a loop is running and not yet started."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    with _QUEUE_LOCK:
        global _QUEUE_DRAINING
        if loop.is_running() and not _QUEUE_DRAINING:
            _QUEUE_DRAINING = True
            loop.create_task(_drain_queue())


__all__ = [
    "transcribe_call",
    "schedule_transcribe_call",
    "ensure_transcription_drainer",
]
