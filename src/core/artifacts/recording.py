"""
Recording manager - orchestrates voice call audio recording.

Coordinates between the SFU adapter (which captures audio) and the
media module (which stores the resulting files), then populates
``recording_ref`` on the linked voice-call artifact so the
transcription worker can find it.

Usage:
    recording_mgr = RecordingManager(db, media, artifact_manager, sfu_adapter, config)
    recording_mgr.start_call_recording(call_id, channel_id, artifact_id, initiator_id)
    # ... call ends ...
    await recording_mgr.stop_call_recording(call_id)
"""

import asyncio
import os
import subprocess
import tempfile
import time
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import utils.logger as logger
from src.core.base import SnowflakeID
from src.core.voice.signaling.sfu.paths import ensure_within_recording_dir

RECORDING_FORMATS = {
    "webm": ("audio/webm", "webm"),
    "opus": ("audio/ogg", "ogg"),
    "wav": ("audio/wav", "wav"),
    "mp4": ("audio/mp4", "mp4"),
}
DEFAULT_RECORDING_FORMAT = "webm"


class RecordingManager:
    """Manages the lifecycle of voice call audio recordings.

    Each call maps to an SFU room (``voice_<channel_id>``).  The manager
    starts recording when a call begins and stops when the last
    participant leaves, then stores the file(s) through the media module
    and sets ``recording_ref`` on the linked artifact.
    """

    def __init__(
        self,
        db: Any,
        media_module: Any,
        artifact_manager: Any,
        sfu_adapter: Any,
        recording_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._db = db
        self._media = media_module
        self._artifact_manager = artifact_manager
        self._sfu = sfu_adapter
        self._config = recording_config or {}

        ext = self._config.get("format", DEFAULT_RECORDING_FORMAT)
        if ext not in RECORDING_FORMATS:
            ext = DEFAULT_RECORDING_FORMAT
        self._recording_format = ext
        self._recording_mime = RECORDING_FORMATS[ext][0]
        self._max_duration_minutes = int(
            self._config.get("max_duration_minutes", 0) or 0
        )
        self._consent_required = bool(self._config.get("consent_required", True))
        self._recording_root = Path(self._get_recording_dir()).expanduser().resolve()

        # call_id -> { room_id, artifact_id, recording_id, initiator_id, channel_id }
        self._active: Dict[int, Dict[str, Any]] = {}
        self._start_times: Dict[int, float] = {}
        # A consent event can arrive while a stop/upload task is still
        # finishing. Keep an explicit restart request so that consent is not
        # lost to that small asynchronous race.
        self._pending_restarts: Dict[int, Dict[str, int | None]] = {}
        # Per-call lifecycle guards.  The public API is synchronous and may be
        # called repeatedly by voice callbacks, so these guards must be set
        # before the first await to prevent duplicate SFU sessions.
        self._starting: set[int] = set()
        self._stopping: set[int] = set()
        self._pending_stops: set[int] = set()

        # Dedicated background event loop used when no loop is running in the
        # calling thread (``asyncio.get_event_loop()`` raises RuntimeError in
        # non-main threads, so we never call it).
        self._bg_loop: Optional[asyncio.AbstractEventLoop] = None
        self._bg_thread: Optional[threading.Thread] = None

    def _background_loop(self) -> asyncio.AbstractEventLoop:
        """Return an event loop owned by a dedicated daemon thread.

        Lazily created on first use; the returned loop stays alive until the
        process exits so fire-and-forget coroutines can be scheduled from any
        thread without ``asyncio.get_event_loop()``.
        """
        loop = self._bg_loop
        if loop is not None and not loop.is_closed():
            return loop
        loop = asyncio.new_event_loop()

        def _run() -> None:
            asyncio.set_event_loop(loop)
            try:
                loop.run_forever()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(f"recording: background loop exited: {exc}")

        thread = threading.Thread(
            target=_run,
            daemon=True,
            name="RecordingBackgroundLoop",
        )
        thread.start()
        self._bg_loop = loop
        self._bg_thread = thread
        return loop

    def _schedule(self, coro) -> None:
        """Schedule a coroutine on a live loop, or the background loop."""
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(coro)
                return
        except RuntimeError:
            pass
        asyncio.run_coroutine_threadsafe(coro, self._background_loop())

    # ------------------------------------------------------------------
    # Public API  (fire-and-forget so the sync voice lifecycle never blocks)
    # ------------------------------------------------------------------

    def start_call_recording(
        self,
        call_id: SnowflakeID,
        channel_id: SnowflakeID,
        artifact_id: Optional[SnowflakeID],
        initiator_id: SnowflakeID,
        *,
        force_restart: bool = False,
    ) -> None:
        """Begin recording a voice call.

        This is fire-and-forget: the actual async work is scheduled on
        the running event loop or deferred to a background thread.
        """
        normalized = {
            "call_id": int(call_id),
            "channel_id": int(channel_id),
            "artifact_id": int(artifact_id) if artifact_id else None,
            "initiator_id": int(initiator_id),
        }
        call_id = normalized["call_id"]
        if force_restart and (call_id in self._active or call_id in self._starting):
            # A restart is only consumed after the old SFU recording has been
            # stopped. If a start is still in flight, coalesce the request;
            # the start task itself will observe the current state.
            self._pending_restarts[call_id] = normalized
            return
        if call_id in self._active or call_id in self._starting:
            return
        self._starting.add(call_id)
        self._schedule(self._do_start_recording(**normalized))

    def stop_call_recording(self, call_id: SnowflakeID) -> None:
        """Finish recording a voice call and persist the file(s).

        Fire-and-forget, same as :meth:`start_call_recording`.
        """
        cid = int(call_id)
        if cid in self._stopping:
            return
        if cid in self._starting:
            # The start task will stop immediately after it records the SFU
            # session, rather than allowing a late start to leak resources.
            self._pending_stops.add(cid)
            return
        if cid not in self._active:
            return
        self._stopping.add(cid)
        self._schedule(self._do_stop_recording(cid))

    # ------------------------------------------------------------------
    # Internal async implementation
    # ------------------------------------------------------------------

    async def _do_start_recording(
        self,
        call_id: int,
        channel_id: int,
        artifact_id: Optional[int],
        initiator_id: int,
    ) -> None:
        # Consent callbacks may race with the initial fire-and-forget start.
        # Never create two SFU recordings for one call.
        if call_id in self._active:
            self._starting.discard(call_id)
            return
        room_id = f"voice_{channel_id}"
        if self._consent_required and not self._call_consent_allows(
            call_id, initiator_id
        ):
            logger.info(
                "recording: consent not recorded for call %s; not starting", call_id
            )
            self._starting.discard(call_id)
            self._pending_stops.discard(call_id)
            self._pending_restarts.pop(call_id, None)
            return
        output_dir = str(self._recording_root)
        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as exc:
            logger.warning(f"recording: cannot create output dir {output_dir}: {exc}")
            self._starting.discard(call_id)
            self._pending_stops.discard(call_id)
            self._pending_restarts.pop(call_id, None)
            return

        try:
            result = await self._sfu.start_recording(room_id, output_dir)
            self._active[call_id] = {
                "room_id": room_id,
                "artifact_id": artifact_id,
                "recording_id": result.get("recording_id", ""),
                "initiator_id": initiator_id,
                "channel_id": channel_id,
            }
            self._start_times[call_id] = time.time()
            logger.info(
                f"recording: started for call {call_id} (room {room_id}, "
                f"files={result.get('file_count', 0)})"
            )
            if self._max_duration_minutes > 0:
                asyncio.create_task(
                    self._check_max_duration(call_id, self._max_duration_minutes)
                )
            self._starting.discard(call_id)
            # A consent event that arrived while start was awaiting the SFU
            # is already represented by this now-authorized recording. Do not
            # restart it a second time.
            self._pending_restarts.pop(call_id, None)
            if call_id in self._pending_stops:
                self._pending_stops.discard(call_id)
                self._stopping.add(call_id)
                await self._do_stop_recording(call_id)
        except Exception as exc:
            self._starting.discard(call_id)
            self._pending_stops.discard(call_id)
            self._pending_restarts.pop(call_id, None)
            logger.warning(f"recording: failed to start for call {call_id}: {exc}")

    async def _check_max_duration(self, call_id: int, max_minutes: int) -> None:
        await asyncio.sleep(max_minutes * 60)
        if call_id in self._active:
            logger.info(f"recording: max duration reached for call {call_id}, stopping")
            self.stop_call_recording(call_id)

    async def _do_stop_recording(self, call_id: int) -> None:
        info = self._active.pop(call_id, None)
        self._start_times.pop(call_id, None)
        if info is None:
            self._stopping.discard(call_id)
            return

        stop_succeeded = False
        try:
            filepaths = await self._sfu.stop_recording(info["room_id"])
            # ``None`` is a valid successful result when the SFU had no files;
            # only an exception means the old session may still be alive.
            stop_succeeded = True
        except Exception as exc:
            logger.warning(f"recording: failed to stop for call {call_id}: {exc}")
            filepaths = None

        if filepaths:
            try:
                await self._store_recording(call_id, info, filepaths)
            except Exception as exc:
                logger.warning(
                    "recording: failed to store segment for call %s: %s",
                    call_id,
                    exc,
                )
        else:
            logger.info(f"recording: call {call_id} has no recorded files")

        self._stopping.discard(call_id)
        # If the SFU stop failed, restore the active entry and retain the
        # restart request. A later stop call can retry safely; starting now
        # could leave two live recordings for the same room.
        restart = self._pending_restarts.get(call_id)
        if not stop_succeeded:
            self._active[call_id] = info
            self._start_times[call_id] = time.time()
            return
        if restart is not None:
            self._pending_restarts.pop(call_id, None)
            restart_call_id = restart.get("call_id")
            restart_channel_id = restart.get("channel_id")
            restart_initiator_id = restart.get("initiator_id")
            if (
                restart_call_id is None
                or restart_channel_id is None
                or restart_initiator_id is None
            ):
                logger.warning(
                    "recording: ignoring invalid queued restart for call %s",
                    call_id,
                )
                return
            self._starting.add(call_id)
            await self._do_start_recording(
                call_id=int(restart_call_id),
                channel_id=int(restart_channel_id),
                artifact_id=restart.get("artifact_id"),
                initiator_id=int(restart_initiator_id),
            )

    async def _store_recording(
        self,
        call_id: int,
        info: Dict[str, Any],
        filepaths: List[str],
    ) -> None:
        """Upload recorded file(s) to the media module and write
        ``recording_ref`` on the artifact."""
        if len(filepaths) == 1:
            await self._upload_single(call_id, info, filepaths[0])
        else:
            combined = await self._try_combine(filepaths)
            if combined:
                await self._upload_single(call_id, info, combined)
                for p in filepaths:
                    self._cleanup_file(p)
            else:
                await self._upload_multi(call_id, info, filepaths)

    async def _upload_single(
        self,
        call_id: int,
        info: Dict[str, Any],
        filepath: str,
    ) -> None:
        """Upload a single recording file and update the artifact."""
        if not os.path.isfile(filepath):
            logger.warning(f"recording: file vanished before upload: {filepath}")
            return

        try:
            fd = os.open(filepath, os.O_RDONLY)
            try:
                os.fsync(fd)
            except OSError:
                pass
            os.close(fd)
        except OSError:
            pass

        try:
            safe_path = ensure_within_recording_dir(filepath, self._recording_root)
            file_size = safe_path.stat().st_size
        except (OSError, ValueError) as exc:
            logger.warning(f"recording: refusing unsafe file {filepath}: {exc}")
            return

        ext = self._recording_format
        filename = f"call_{call_id}.{ext}"
        try:
            with safe_path.open("rb") as fh:
                if hasattr(self._media, "upload_stream"):
                    upload_result = self._media.upload_stream(
                        user_id=info["initiator_id"],
                        stream=fh,
                        filename=filename,
                        content_type=self._recording_mime,
                        size=file_size,
                    )
                else:
                    upload_result = self._media.upload_file(
                        user_id=info["initiator_id"],
                        file_data=fh.read(),
                        filename=filename,
                        content_type=self._recording_mime,
                    )
        except Exception as exc:
            logger.warning(f"recording: media upload failed for call {call_id}: {exc}")
            return

        self._update_artifact_payload(
            info["artifact_id"],
            recording_ref=upload_result.url,
            recording_file_id=upload_result.file_id,
        )

        self._cleanup_file(filepath)
        logger.info(
            f"recording: stored call {call_id} -> "
            f"file_id={upload_result.file_id}, url={upload_result.url}"
        )

    async def _upload_multi(
        self,
        call_id: int,
        info: Dict[str, Any],
        filepaths: List[str],
    ) -> None:
        """Upload multiple recording files (when combining failed)."""
        urls: List[str] = []
        file_ids: List[int] = []
        ext = self._recording_format
        for idx, fp in enumerate(filepaths):
            if not os.path.isfile(fp):
                continue
            try:
                fd = os.open(fp, os.O_RDONLY)
                try:
                    os.fsync(fd)
                except OSError:
                    pass
                os.close(fd)
            except OSError:
                pass
            try:
                safe_path = ensure_within_recording_dir(fp, self._recording_root)
                file_size = safe_path.stat().st_size
            except (OSError, ValueError) as exc:
                logger.warning("recording: refusing unsafe track %s: %s", fp, exc)
                continue

            filename = f"call_{call_id}_track_{idx}.{ext}"
            try:
                with safe_path.open("rb") as fh:
                    if hasattr(self._media, "upload_stream"):
                        result = self._media.upload_stream(
                            user_id=info["initiator_id"],
                            stream=fh,
                            filename=filename,
                            content_type=self._recording_mime,
                            size=file_size,
                        )
                    else:
                        result = self._media.upload_file(
                            user_id=info["initiator_id"],
                            file_data=fh.read(),
                            filename=filename,
                            content_type=self._recording_mime,
                        )
                urls.append(result.url)
                file_ids.append(result.file_id)
            except Exception as exc:
                logger.warning(f"recording: multi-upload track {idx} failed: {exc}")

            self._cleanup_file(fp)

        if urls:
            self._update_artifact_payload(
                info["artifact_id"],
                recording_ref=urls[0],
                recording_file_id=file_ids[0] if file_ids else None,
                recording_urls=urls,
                recording_file_ids=file_ids,
            )

    async def _try_combine(self, filepaths: List[str]) -> Optional[str]:
        """Try to combine multiple WebM files with ffmpeg.

        Returns the path to the combined file, or ``None`` if combining
        is not possible.
        """
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return None
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None

        combine_dir = str(Path(filepaths[0]).expanduser().resolve().parent)
        fd, out_path = tempfile.mkstemp(
            suffix=".webm", prefix="recording_", dir=combine_dir
        )
        os.close(fd)

        filter_parts = []
        input_args = []
        for i, fp in enumerate(filepaths):
            input_args.extend(["-i", fp])
            filter_parts.append(f"[{i}:a]")

        filter_str = (
            "".join(filter_parts) + f"amix=inputs={len(filepaths)}:duration=longest[a]"
        )

        cmd = (
            ["ffmpeg", "-y"]
            + input_args
            + ["-filter_complex", filter_str]
            + ["-map", "[a]", "-c:a", "libopus", out_path]
        )

        try:
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
                return out_path
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.debug(f"recording: ffmpeg combine failed: {exc}")

        self._cleanup_file(out_path)
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_recording_dir(self) -> str:
        default = os.path.join(self._config.get("base_path", ""), "recordings")
        return str(
            Path(self._config.get("output_dir") or default).expanduser().resolve()
        )

    def _call_consent_allows(self, call_id: int, initiator_id: int) -> bool:
        """Return whether every live voice-channel member has consented.

        ``voice_states`` is the live membership authority; an empty or failed
        lookup fails closed. ``initiator_id`` is retained for API compatibility
        and logging context only.
        """
        try:
            from .repository import get_voice_call

            call = get_voice_call(self._db, call_id)
            if call is None:
                return False
            raw_consented = getattr(call, "consented_participants", []) or []
            if not isinstance(raw_consented, (list, tuple, set)):
                return False
            consented = {int(value) for value in raw_consented}
            current_ids = {
                int(row["user_id"])
                for row in self._db.fetch_all(
                    "SELECT user_id FROM voice_states WHERE channel_id = ?",
                    (call.channel_id,),
                )
                or []
            }
            # An unavailable/empty live roster must fail closed. Recording a
            # call based only on the initiator ID would bypass participant
            # consent during a membership lookup failure.
            if not current_ids:
                return False
            return current_ids.issubset(consented)
        except (TypeError, ValueError) as exc:
            logger.warning(
                "recording: invalid consent state for call %s: %s", call_id, exc
            )
            return False
        except Exception as exc:
            logger.warning(
                "recording: consent lookup failed for call %s: %s", call_id, exc
            )
            return False

    def _update_artifact_payload(
        self,
        artifact_id: Optional[int],
        **fields: Any,
    ) -> None:
        if artifact_id is None or self._artifact_manager is None:
            return
        try:
            existing = self._artifact_manager.get(artifact_id)
            if existing is None:
                return
            payload = dict(existing.payload or {})
            # A consent interruption can split one call into multiple SFU
            # segments. Preserve the first segment and accumulate later ones
            # instead of silently overwriting the audit/playback references.
            if fields.get("recording_ref"):
                previous_urls = list(payload.get("recording_urls") or [])
                previous_ref = payload.get("recording_ref")
                if previous_ref and previous_ref not in previous_urls:
                    previous_urls.insert(0, previous_ref)
                if fields["recording_ref"] not in previous_urls:
                    previous_urls.append(fields["recording_ref"])
                fields = dict(fields)
                fields["recording_urls"] = previous_urls
                previous_ids = list(payload.get("recording_file_ids") or [])
                previous_id = payload.get("recording_file_id")
                if previous_id and previous_id not in previous_ids:
                    previous_ids.insert(0, previous_id)
                new_id = fields.get("recording_file_id")
                if new_id and new_id not in previous_ids:
                    previous_ids.append(new_id)
                fields["recording_file_ids"] = previous_ids
            payload.update(fields)
            self._artifact_manager.update(
                artifact_id,
                payload=payload,
                recorded=True,
            )
        except Exception as exc:
            logger.warning(f"recording: failed to update artifact {artifact_id}: {exc}")

    @staticmethod
    def _cleanup_file(filepath: str) -> None:
        try:
            if os.path.isfile(filepath):
                os.remove(filepath)
        except OSError:
            pass
