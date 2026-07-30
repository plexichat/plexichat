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
from typing import Any, Dict, List, Optional

import utils.logger as logger
from src.core.base import SnowflakeID

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

        # call_id -> { room_id, artifact_id, recording_id, initiator_id, channel_id }
        self._active: Dict[int, Dict[str, Any]] = {}
        self._start_times: Dict[int, float] = {}

    # ------------------------------------------------------------------
    # Public API  (fire-and-forget so the sync voice lifecycle never blocks)
    # ------------------------------------------------------------------

    def start_call_recording(
        self,
        call_id: SnowflakeID,
        channel_id: SnowflakeID,
        artifact_id: Optional[SnowflakeID],
        initiator_id: SnowflakeID,
    ) -> None:
        """Begin recording a voice call.

        This is fire-and-forget: the actual async work is scheduled on
        the running event loop or deferred to a background thread.
        """
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(
                    self._do_start_recording(
                        int(call_id),
                        int(channel_id),
                        int(artifact_id) if artifact_id else None,
                        int(initiator_id),
                    )
                )
                return
        except RuntimeError:
            pass

        logger.debug(f"recording: no running loop for call {call_id}; enqueuing start")
        asyncio.run_coroutine_threadsafe(
            self._do_start_recording(
                int(call_id),
                int(channel_id),
                int(artifact_id) if artifact_id else None,
                int(initiator_id),
            ),
            asyncio.get_event_loop(),
        )

    def stop_call_recording(self, call_id: SnowflakeID) -> None:
        """Finish recording a voice call and persist the file(s).

        Fire-and-forget, same as :meth:`start_call_recording`.
        """
        cid = int(call_id)
        if cid not in self._active:
            return

        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(self._do_stop_recording(cid))
                return
        except RuntimeError:
            pass

        asyncio.run_coroutine_threadsafe(
            self._do_stop_recording(cid),
            asyncio.get_event_loop(),
        )

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
        room_id = f"voice_{channel_id}"
        output_dir = self._get_recording_dir()
        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as exc:
            logger.warning(f"recording: cannot create output dir {output_dir}: {exc}")
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
        except Exception as exc:
            logger.warning(f"recording: failed to start for call {call_id}: {exc}")

    async def _check_max_duration(self, call_id: int, max_minutes: int) -> None:
        await asyncio.sleep(max_minutes * 60)
        if call_id in self._active:
            logger.info(f"recording: max duration reached for call {call_id}, stopping")
            await self._do_stop_recording(call_id)

    async def _do_stop_recording(self, call_id: int) -> None:
        info = self._active.pop(call_id, None)
        self._start_times.pop(call_id, None)
        if info is None:
            return

        try:
            filepaths = await self._sfu.stop_recording(info["room_id"])
        except Exception as exc:
            logger.warning(f"recording: failed to stop for call {call_id}: {exc}")
            return

        if not filepaths:
            logger.info(f"recording: call {call_id} has no recorded files")
            return

        await self._store_recording(call_id, info, filepaths)

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
            with open(filepath, "rb") as fh:
                file_data = fh.read()
        except OSError as exc:
            logger.warning(f"recording: cannot read {filepath}: {exc}")
            return

        ext = self._recording_format
        filename = f"call_{call_id}.{ext}"
        try:
            upload_result = self._media.upload_file(
                user_id=info["initiator_id"],
                file_data=file_data,
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
            recording_path=filepath,
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
        primary = filepaths[0]

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
                with open(fp, "rb") as fh:
                    file_data = fh.read()
            except OSError:
                continue

            filename = f"call_{call_id}_track_{idx}.{ext}"
            try:
                result = self._media.upload_file(
                    user_id=info["initiator_id"],
                    file_data=file_data,
                    filename=filename,
                    content_type=self._recording_mime,
                )
                urls.append(result.url)
                file_ids.append(result.file_id)
            except Exception as exc:
                logger.warning(f"recording: multi-upload track {idx} failed: {exc}")

            self._cleanup_file(fp)

        if urls:
            primary_path = primary if os.path.isfile(primary) else None
            self._update_artifact_payload(
                info["artifact_id"],
                recording_ref=urls[0],
                recording_file_id=file_ids[0] if file_ids else None,
                recording_path=primary_path,
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

        fd, out_path = tempfile.mkstemp(suffix=".webm", prefix="recording_")
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
        return self._config.get("output_dir") or default

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
