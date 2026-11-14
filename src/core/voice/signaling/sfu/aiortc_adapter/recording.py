"""Room recording for the aiortc SFU adapter."""

from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List, Optional

import utils.logger as logger

from ...exceptions import SFUConnectionError
from .models import AiortcPeer
from .runtime import (
    _aiortc_recording_available,
    _make_media_recorder,
    _media_recorder_cls,
)
from .validation import validate_recording_output_dir, validate_room_id


async def record_audio_track(
    recording: Optional[Dict[str, Any]],
    room_id: str,
    peer_id: str,
    track: Any,
) -> None:
    """Start a recorder for one arriving audio track while the room records.

    A no-op when the room is not recording or MediaRecorder is missing, so an
    arriving track never fails because recording is unavailable.
    """
    if recording is None or not _aiortc_recording_available():
        return
    # An explicit capability check rather than ``assert``: assertions are
    # stripped under ``python -O``, and the recorder would then fail deep
    # inside MediaRecorder instead of being skipped with a diagnosis.
    if _media_recorder_cls() is None:
        logger.warning("Recording requested but MediaRecorder is not available")
        return

    rec_producer_id = str(uuid.uuid4())
    filepath = os.path.join(
        recording["output_dir"],
        f"{recording['recording_id']}_{peer_id}_{rec_producer_id}.webm",
    )
    try:
        recorder = _make_media_recorder(filepath)
        recorder.addTrack(track)
        await recorder.start()
    except Exception as exc:
        logger.warning(f"Failed to auto-record track for peer {peer_id}: {exc}")
        return

    recording["recorders"].append(
        {
            "producer_id": rec_producer_id,
            "peer_id": peer_id,
            "filepath": filepath,
            "recorder": recorder,
        }
    )
    logger.info(f"Auto-recorded audio track for peer {peer_id} in room {room_id}")


class RecordingMixin:
    """Recording a room, and the auto-recording of arriving audio tracks."""

    _rooms: Dict[str, Dict[str, AiortcPeer]]
    _recordings: Dict[str, Dict[str, Any]]

    async def start_recording(self, room_id: str, output_dir: str) -> Dict[str, Any]:
        """Start recording audio in a room."""
        room_id = validate_room_id(room_id)
        if room_id not in self._rooms:
            raise SFUConnectionError(f"Room {room_id} does not exist")
        if room_id in self._recordings:
            logger.warning("Room is already recording")
            return {"recording_id": self._recordings[room_id]["recording_id"]}
        if not _aiortc_recording_available():
            logger.warning("Recording requested but MediaRecorder is not available")
            return {"recording_id": "", "file_count": 0, "unsupported": True}
        safe_dir = validate_recording_output_dir(output_dir)
        recording_id = str(uuid.uuid4())
        os.makedirs(safe_dir, exist_ok=True)
        recorders: List[Dict[str, Any]] = []
        for pid, peer in self._rooms[room_id].items():
            for _idx, track in enumerate(peer.incoming_audio_tracks):
                rec_producer_id = str(uuid.uuid4())
                filename = f"{recording_id}_{pid}_{rec_producer_id}.webm"
                filepath = os.path.normpath(os.path.join(safe_dir, filename))
                if not (filepath == safe_dir or filepath.startswith(safe_dir + os.sep)):
                    continue
                try:
                    recorder = _make_media_recorder(filepath)
                    recorder.addTrack(track)
                    await recorder.start()
                    recorders.append(
                        {
                            "producer_id": rec_producer_id,
                            "peer_id": pid,
                            "filepath": filepath,
                            "recorder": recorder,
                        }
                    )
                    logger.debug("Recording track started")
                except Exception as exc:
                    logger.warning("Failed to start recorder: %s", type(exc).__name__)
        self._recordings[room_id] = {
            "recording_id": recording_id,
            "output_dir": safe_dir,
            "recorders": recorders,
        }
        logger.info("Started recording: %d track(s)", len(recorders))
        return {"recording_id": recording_id, "file_count": len(recorders)}

    async def stop_recording(self, room_id: str) -> Optional[List[str]]:
        """Stop recording a room and return the recorded file paths."""
        if room_id not in self._recordings:
            logger.info(f"Room {room_id} is not recording")
            return None

        info = self._recordings.pop(room_id)
        filepaths: List[str] = []
        for rec_info in info["recorders"]:
            try:
                await rec_info["recorder"].stop()
                filepaths.append(rec_info["filepath"])
                logger.debug(
                    f"Stopped recorder for peer {rec_info['peer_id']}: "
                    f"{rec_info['filepath']}"
                )
            except Exception as exc:
                logger.warning(
                    f"Failed to stop recorder for producer "
                    f"{rec_info.get('producer_id', '?')}: {exc}"
                )

        logger.info(f"Stopped recording room {room_id}: {len(filepaths)} file(s)")
        return filepaths if filepaths else None
