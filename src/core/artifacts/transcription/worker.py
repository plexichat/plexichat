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
:func:`schedule_transcribe_call`.

Scheduling is deliberately bounded: every call (async *and* synchronous
contexts) goes through one bounded in-process queue with a call-id dedupe set
and a hard concurrency ceiling, and each job runs under a wall-clock deadline.
A burst of call-end events can therefore never spawn an unlimited number of
provider tasks, repeat the same call, or let one hung provider stall the
drainer indefinitely. Overload sheds jobs instead of growing memory.
"""

import asyncio
import os
import queue
import shutil
import tempfile
import threading
from typing import Any, BinaryIO, Dict, List, Optional, Set, Tuple, cast
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


def _resolve_local_recording_path(
    ref: str, call_id: Optional[SnowflakeID] = None
) -> Optional[str]:
    """Accept a local recording path only when it lives inside the configured
    recording root and (when the call is known) matches that call's files.

    Artifact payloads are user-editable, so a ``recording_ref`` must never be
    treated as an arbitrary server-side path: that would let an artifact author
    read any file the process can access (e.g. via the OpenAI upload or local
    Whisper).
    """
    try:
        import utils.config as cfg
        from src.core.voice.signaling.sfu.paths import ensure_within_recording_dir

        artifacts_cfg = cfg.get("artifacts", {}) or {}
        root = (
            (artifacts_cfg.get("voice", {}) or {})
            .get("recording", {})
            .get("output_dir")
        )
        if not root:
            return None
        safe_path = ensure_within_recording_dir(
            ref, os.path.realpath(os.path.expanduser(str(root)))
        )
    except Exception:
        return None
    if call_id is not None:
        # M27: multi-segment recordings are stored as
        # ``call_<id>_track_<n>.<ext>`` (when ffmpeg combining fails). The
        # previous ``call_<id>.`` prefix check rejected every track file, so
        # consent-interrupted segmented calls were NEVER transcribed. Accept
        # both the single-file ``call_<id>.`` form and the ``call_<id>_track_``
        # form so the first available segment resolves.
        name = safe_path.name
        prefix = f"call_{int(call_id)}"
        if not (name.startswith(prefix + ".") or name.startswith(prefix + "_track_")):
            return None
    return str(safe_path)


def _resolve_recording_ref(
    call_payload: Dict[str, Any],
    db: Any,
    provider_name: str,
    call_id: Optional[SnowflakeID] = None,
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
        return _resolve_local_recording_path(ref, call_id), None

    file_id = call_payload.get("recording_file_id")
    if not file_id:
        # Local Whisper/OpenAI cannot consume a relative API URL. Do not
        # fail open into a provider error; the worker will skip this call.
        return None, None

    temporary_path = _materialize_media_file(int(file_id))
    if temporary_path is None:
        return None, None
    return temporary_path, temporary_path


def _materialize_media_file(file_id: int) -> Optional[str]:
    """Copy a media object to a temporary local file; return its path.

    Returns ``None`` when the media module is unavailable, the object cannot
    be streamed/read, or the write fails. The caller owns unlinking the
    returned path.
    """
    temporary_path: Optional[str] = None
    try:
        import src.api as api

        media = api.get_media()
        if media is None:
            return None
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
                    return handle.name
            finally:
                close = getattr(stream, "close", None)
                if callable(close):
                    close()

        get_data = getattr(media, "get_file_data", None)
        if not callable(get_data):
            return None
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
            return handle.name
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
        return None


def _recording_segment_file_ids(call_payload: Dict[str, Any]) -> List[int]:
    """Return the ordered media file ids of a multi-segment recording.

    RecordingManager stores consent-interrupted recordings as several media
    objects when ffmpeg combining fails; the artifact payload then carries
    ``recording_file_ids`` with one entry per track. Single-segment
    recordings (and malformed/empty lists) return ``[]`` so callers fall back
    to the classic single-reference resolution.
    """
    raw_ids = call_payload.get("recording_file_ids")
    if not isinstance(raw_ids, (list, tuple)) or len(raw_ids) <= 1:
        return []
    ids: List[int] = []
    for raw in raw_ids:
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    return ids


def _resolve_local_recording_tracks(
    ref: str, call_id: Optional[SnowflakeID] = None
) -> List[str]:
    """Expand a local multi-segment recording ref into every track file.

    RecordingManager writes each consent-interrupted segment as
    ``call_<id>_track_<n>.<ext>`` under the recording root. When ``ref`` names
    one of those track files, return the full ordered track list (M27); a
    plain single-file ``call_<id>.`` ref returns ``[ref]`` when it passes the
    containment check. An unsafe/unknown path returns ``[]``.
    """
    try:
        import utils.config as cfg
        from src.core.voice.signaling.sfu.paths import ensure_within_recording_dir

        artifacts_cfg = cfg.get("artifacts", {}) or {}
        root = (
            (artifacts_cfg.get("voice", {}) or {})
            .get("recording", {})
            .get("output_dir")
        )
        if not root:
            return []
        safe_path = ensure_within_recording_dir(
            ref, os.path.realpath(os.path.expanduser(str(root)))
        )
    except Exception:
        return []
    if call_id is None:
        return []
    prefix = f"call_{int(call_id)}"
    name = safe_path.name
    if name.startswith(prefix + "."):
        return [str(safe_path)]
    if not name.startswith(prefix + "_track_"):
        return []

    track_prefix = prefix + "_track_"
    indexed: List[Tuple[int, Any]] = []
    try:
        for child in safe_path.parent.iterdir():
            child_name = child.name
            if not child_name.startswith(track_prefix):
                continue
            if child.suffix != safe_path.suffix:
                continue
            try:
                idx = int(child_name[len(track_prefix) :].split(".", 1)[0])
            except ValueError:
                continue
            indexed.append((idx, child))
    except OSError:
        return []
    indexed.sort(key=lambda item: item[0])
    return [str(child) for _, child in indexed]


def _resolve_recording_segments(
    call_payload: Dict[str, Any],
    db: Any,
    provider_name: str,
    call_id: Optional[SnowflakeID] = None,
) -> List[Tuple[Optional[str], Optional[str]]]:
    """Resolve a call recording into one entry per media segment (M27).

    Consent-interrupted calls produce several SFU segments. RecordingManager
    concatenates them with ffmpeg when possible, but when combining fails the
    payload carries ``recording_file_ids`` (2+) and the worker must transcribe
    EVERY segment - not just the first - or the transcript silently omits
    most of the call.    Returns a list of ``(recording_ref, temporary_ref)`` pairs in recording
    order (``temporary_ref`` is non-None only for media objects materialized
    to a temp file; the caller unlinks it). Single-segment recordings resolve
    exactly like :func:`_resolve_recording_ref`. Entries that fail to resolve
    are skipped, so a partially-available recording still yields whatever
    segments can be transcribed.
    """
    segment_ids = _recording_segment_file_ids(call_payload)
    if not segment_ids:
        single = _resolve_recording_ref(call_payload, db, provider_name, call_id)
        return [single] if single[0] is not None else []

    ref = call_payload.get("recording_ref")
    if isinstance(ref, str) and os.path.isfile(ref):
        # Local multi-segment recordings: transcribe every sibling track file.
        return [
            (track, None) for track in _resolve_local_recording_tracks(ref, call_id)
        ]

    results: List[Tuple[Optional[str], Optional[str]]] = []
    for fid in segment_ids:
        # Narrow the payload to this one segment so per-provider resolution
        # (Azure signing, media materialization) runs exactly as it does for a
        # single-file recording. The shared ref only names the FIRST track;
        # forwarding it would re-resolve track 0 for every segment, so replace
        # it with a synthetic relative ref that routes resolution through the
        # segment's own file id (the ref value is unused once a file id is
        # present, but the resolver requires a non-empty string).
        segment_payload = dict(call_payload)
        segment_payload["recording_file_id"] = fid
        segment_payload["recording_ref"] = f"/media/segment-{fid}.webm"
        resolved = _resolve_recording_ref(segment_payload, db, provider_name, call_id)
        if resolved[0] is not None:
            results.append(resolved)
    return results


def _merge_transcription_results(
    results: List[TranscriptionResult],
) -> TranscriptionResult:
    """Merge per-segment transcription results into one coherent transcript.

    The segments of a consent-interrupted call are consecutive recordings of
    the same conversation, so each later result's timestamps are offset by the
    cumulative duration of the segments before it (the previous result's final
    segment end time). Speaker labels and per-segment text pass through
    unchanged. A single result is returned untouched.
    """
    if not results:
        return TranscriptionResult()
    if len(results) == 1:
        return results[0]

    merged: List[Dict[str, Any]] = []
    offset = 0.0
    language = "unknown"
    for result in results:
        if result.language and result.language != "unknown":
            if language == "unknown":
                language = result.language
        for seg in result.segments:
            if not isinstance(seg, dict):
                continue
            shifted = dict(seg)
            try:
                start = float(seg.get("start") or 0.0)
                end = float(seg.get("end") or 0.0)
            except (TypeError, ValueError):
                start, end = 0.0, 0.0
            shifted["start"] = round(start + offset, 3)
            shifted["end"] = round(end + offset, 3)
            merged.append(shifted)
            offset = max(offset, shifted["end"])

    text = " ".join(
        str(seg.get("text") or "").strip()
        for seg in merged
        if str(seg.get("text") or "").strip()
    )
    return TranscriptionResult(segments=merged, language=language, text=text)


def _consent_allows(call: Any, participant_ids: Optional[List[Any]] = None) -> bool:
    """Decide whether consent permits transcription of this call.

    Policy (aligned with RecordingManager's all-consenting rule): EVERY
    distinct participant of the call must have consented — never merely one.
    Consent is required unless the server config opts out
    (``consent_required`` is explicitly ``False``), mirroring the recording
    gate.

    When a persisted participant ID roster is supplied (the DISTINCT identity
    roster kept on the linked artifact payload by ``VoiceCallManager``), it is
    authoritative: every roster member must appear in ``consented_participants``
    (roster ⊆ consented). This is immune to member-churn attacks that defeat a
    max-concurrent COUNT. An empty/malformed roster fails closed.

    Without any roster (e.g. calls created before roster persistence existed),
    unanimity falls back to the count comparison against the call's persisted
    ``participant_count``; a consent roster covering fewer participants than
    joined fails closed there too.
    """
    transcription_cfg = _transcription_config()
    consent_required = transcription_cfg.get("consent_required", True)
    if consent_required is False:
        return True
    consented = getattr(call, "consented_participants", None) or []
    if not isinstance(consented, (list, tuple, set)):
        return False
    try:
        distinct_consented = {int(value) for value in consented}
    except (TypeError, ValueError):
        return False

    if participant_ids is not None:
        try:
            roster = {int(value) for value in participant_ids}
        except (TypeError, ValueError):
            return False
        if not roster:
            # No verifiable roster: fail closed rather than transcribing on a
            # single stray consent.
            return False
        return roster.issubset(distinct_consented)

    participant_count = int(getattr(call, "participant_count", 0) or 0)
    if participant_count <= 0:
        return False
    return len(distinct_consented) >= participant_count


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
                # SECURITY: get_participant_ids() is the raw
                # msg_participants.user_id projection with no type filtering.
                # int() laundering ("07"->7, 7.9->7, " 8 "->8, True->1) would
                # add a recipient that was never a genuine participant to the
                # ARTIFACT_UPDATE event fan-out. type() also rejects bool.
                user_ids = [
                    u
                    for u in messaging_mod.get_participant_ids(conversation_id)
                    if type(u) is int
                ]
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(f"transcription: participant resolve failed: {exc}")
            # Fail closed: None lets the router widen this server-scoped event
            # to every member of the server instead of the conversation's
            # verified participants.
            user_ids = []

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

        # L28: enforce the documented ``max_audio_minutes`` soft cap (previously
        # configured but NEVER read, so unbounded audio reached providers).
        # Skip transcription when the call's recorded duration exceeds the cap;
        # a missing/unknown duration is allowed through (fail open on data
        # availability, not on policy).
        max_audio_minutes = transcription_cfg.get("max_audio_minutes")
        if max_audio_minutes is not None:
            try:
                cap_seconds = float(max_audio_minutes) * 60.0
            except (TypeError, ValueError):
                cap_seconds = 0.0
            if cap_seconds > 0:
                duration_seconds = getattr(call, "duration_seconds", None) or 0
                try:
                    duration_seconds = float(duration_seconds)
                except (TypeError, ValueError):
                    duration_seconds = 0.0
                if duration_seconds > cap_seconds:
                    logger.warning(
                        f"transcription: call {call_id} duration "
                        f"{duration_seconds:.0f}s exceeds max_audio_minutes "
                        f"({max_audio_minutes}); skip."
                    )
                    return None

        # Load the linked artifact BEFORE the consent gate: its persisted
        # participant ID roster (distinct identities observed on the call)
        # makes the unanimity check churn-proof.
        artifact = None
        if call.artifact_id is not None:
            artifact = get_artifact(db, call.artifact_id)

        participant_roster: Optional[List[Any]] = None
        if artifact is not None and isinstance(artifact.payload, dict):
            raw_roster = artifact.payload.get("participant_ids")
            if isinstance(raw_roster, (list, tuple)):
                participant_roster = list(raw_roster)

        if not _consent_allows(call, participant_roster):
            logger.info(f"transcription: call {call_id} lacks consent; skip (GDPR).")
            return None

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

        # M27: a consent-interrupted call may have several SFU segments
        # (ffmpeg combining failed at record time). Resolve EVERY segment and
        # transcribe each one, then merge the results with cumulative time
        # offsets so the transcript covers the whole call - not just the first
        # track. Single-segment recordings keep the exact single-file path.
        resolved_segments = _resolve_recording_segments(
            call_payload,
            db,
            str(transcription_cfg.get("provider", "local_whisper")),
            call_id=call_id,
        )
        if not resolved_segments:
            logger.warning(f"transcription: call {call_id} has no recording_ref; skip.")
            return None

        opts: Dict[str, Any] = {
            "language": transcription_cfg.get("language", "auto"),
            "diarize": transcription_cfg.get("diarize", False),
        }
        logger.info(
            f"transcription: running provider for call {call_id} over "
            f"{len(resolved_segments)} segment(s)..."
        )
        transcribed: List[TranscriptionResult] = []
        try:
            for recording_ref, temporary_ref in resolved_segments:
                if recording_ref is None:
                    continue
                try:
                    segment_result: TranscriptionResult = await provider.transcribe(
                        recording_ref, opts
                    )
                finally:
                    if temporary_ref:
                        try:
                            os.unlink(temporary_ref)
                        except OSError:
                            pass
                transcribed.append(segment_result)
        finally:
            # Never leak temp files if a later segment raises mid-loop.
            for _recording_ref, temporary_ref in resolved_segments:
                if temporary_ref:
                    try:
                        os.unlink(temporary_ref)
                    except OSError:
                        pass
        if not transcribed:
            logger.warning(f"transcription: call {call_id} produced no results; skip.")
            return None
        result = _merge_transcription_results(transcribed)

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


# === Bounded, deduplicated fire-and-forget scheduling ===

# One stdlib ``queue.Queue`` is shared by ALL schedule paths (async request
# handlers and synchronous lifecycle hooks alike). M26: stdlib (not
# ``asyncio.Queue``) keeps ``put_nowait`` safe from arbitrary sync threads and
# lets the queue be created without a running loop; the async drainer polls it
# without ever blocking the loop.
#
# Scaling hardening: previously the async path called ``loop.create_task``
# directly, so a burst of call-end events could spawn unlimited provider tasks
# with no dedupe and no deadline. Both paths now enqueue onto the same bounded
# queue, dedupe by call id (queued OR running), cap concurrent jobs and give
# each job a wall-clock timeout. Overload sheds work instead of growing memory.
_QUEUE = None
_QUEUE_DRAINING = False
_QUEUE_LOCK = threading.Lock()
_QUEUE_MAX = 512

# Hard concurrency ceiling for provider calls (back-pressure). Each call holds
# a provider connection and writes an artifact, so unbounded fan-out exhausts
# both the pool and the provider. Env overrides keep deployments tunable.
_MAX_ACTIVE = max(1, int(os.environ.get("PLEXICHAT_TRANSCRIPTION_MAX_ACTIVE", "4")))
# Per-job deadline. A hung provider can no longer pin a drainer slot forever.
_JOB_TIMEOUT = max(
    1.0, float(os.environ.get("PLEXICHAT_TRANSCRIPTION_TIMEOUT_SECONDS", "300"))
)
_DRAIN_POLL_INTERVAL = 0.05

# call ids currently queued or running. Guarded by ``_QUEUE_LOCK``; a duplicate
# schedule for a call already in flight is dropped instead of consuming a second
# queue slot or running twice in parallel.
_INFLIGHT: Set[Any] = set()


def _get_queue():
    global _QUEUE
    if _QUEUE is None:
        _QUEUE = queue.Queue(maxsize=_QUEUE_MAX)
    return _QUEUE


async def _run_job(job: Dict[str, Any]) -> None:
    """Run one queued job under a deadline and release its dedupe claim."""
    call_id = job["call_id"]
    try:
        await asyncio.wait_for(
            transcribe_call(call_id, job["db"], job["config"]),
            timeout=_JOB_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(
            f"transcription: call {call_id} exceeded {_JOB_TIMEOUT:g}s timeout"
        )
    except asyncio.CancelledError:  # pragma: no cover - loop shutdown
        raise
    except Exception as exc:  # never crash the drainer
        logger.error(f"transcription: queued job for call {call_id} failed: {exc}")
    finally:
        _get_queue().task_done()
        with _QUEUE_LOCK:
            _INFLIGHT.discard(call_id)


async def _drain_queue() -> None:
    """Drain the bounded queue with capped concurrency and per-job deadlines."""
    tasks: Set[asyncio.Future] = set()
    try:
        while True:
            # Reap finished jobs; _run_job already logged any failure.
            for task in [t for t in tasks if t.done()]:
                tasks.discard(task)
                try:
                    task.result()
                except asyncio.CancelledError:  # pragma: no cover - shutdown
                    raise
                except Exception:  # pragma: no cover - defensive
                    pass

            with _QUEUE_LOCK:
                free = _MAX_ACTIVE - len(tasks)
            for _ in range(max(0, free)):
                try:
                    job = _get_queue().get_nowait()
                except queue.Empty:
                    break
                tasks.add(asyncio.ensure_future(_run_job(job)))

            if not tasks and _get_queue().empty():
                break
            # Poll the thread-safe stdlib queue; never block the loop on a
            # blocking ``queue.get()``.
            await asyncio.sleep(_DRAIN_POLL_INTERVAL)
    except asyncio.CancelledError:  # pragma: no cover - shutdown
        pass
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        with _QUEUE_LOCK:
            global _QUEUE_DRAINING
            _QUEUE_DRAINING = False
            # A job may have been enqueued during shutdown; hand it to a fresh
            # drainer so it is never stranded in the bounded queue.
            pending = not _get_queue().empty()
        if pending:
            ensure_transcription_drainer()


def schedule_transcribe_call(
    call_id: SnowflakeID, db: Any, config_arg: Any = None
) -> None:
    """Schedule :func:`transcribe_call` without blocking the caller.

    Always enqueues onto the single bounded queue (never ``create_task``
    directly), so async request handlers and sync hooks share the same caps.
    Jobs are shed when the queue is full or the call id is already in flight,
    and each runs under :data:`_JOB_TIMEOUT`. The DB row remains, so a later
    sweep can retry a shed job.
    """
    transcription_cfg = (
        config_arg if config_arg is not None else _transcription_config()
    )
    q = _get_queue()
    with _QUEUE_LOCK:
        if call_id in _INFLIGHT:
            logger.debug(
                f"transcription: duplicate job for call {call_id} dropped (in flight)"
            )
            return
        try:
            q.put_nowait({"call_id": call_id, "db": db, "config": transcription_cfg})
        except queue.Full:
            logger.warning(
                f"transcription: queue full ({_QUEUE_MAX}); dropped job for call {call_id}"
            )
            return
        _INFLIGHT.add(call_id)
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
