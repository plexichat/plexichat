"""Recording lifecycle for the mediasoup WebSocket adapter."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import utils.logger as logger

from ..base import MediaKind, TransportDirection
from .core import MediasoupWSAdapterCore


def _recording_path_guards() -> tuple[
    Callable[[str], Path],
    Callable[[str | Path, str | Path], Path],
    Callable[[str], str],
]:
    """Resolve the recording path guards through the package namespace.

    ``stop_recording`` reads recording files under the guarded directory, and
    tests monkeypatch
    ``src.core.voice.signaling.sfu.mediasoup_ws.ensure_within_recording_dir``
    to exercise the failure path. Resolving the helpers per call keeps that
    patch point effective; binding them at import time would silently ignore
    it.
    """
    from . import (
        ensure_within_recording_dir,
        resolve_recording_dir,
        safe_filename_component,
    )

    return (
        resolve_recording_dir,
        ensure_within_recording_dir,
        safe_filename_component,
    )


class RecordingMixin(MediasoupWSAdapterCore):
    """Start/stop recordings and retain unrecoverable teardown diagnostics."""

    async def start_recording(self, room_id: str, output_dir: str) -> Dict[str, Any]:
        """Start recording by creating a recv transport and consuming all audio."""
        resolve_dir, _ensure_within, safe_component = _recording_path_guards()
        safe_output_dir = resolve_dir(output_dir)
        recording_id = f"rec_{safe_component(room_id)}_{int(time.time())}"

        # Find a peer connection in this room to use for the recorder session
        key = next(
            (k for k in self._connections if k.startswith(f"{room_id}:")),
            None,
        )
        if key is None:
            logger.warning(f"No connections in room {room_id} to attach recorder to")
            self._recordings[room_id] = {
                "recording_id": recording_id,
                "output_dir": str(safe_output_dir),
                "file_count": 0,
                "transport_id": None,
                "consumer_ids": [],
                "recorder_peer_id": None,
            }
            return {"recording_id": recording_id, "file_count": 0}

        connection = self._connections[key]

        try:
            os.makedirs(safe_output_dir, exist_ok=True)
        except OSError:
            pass

        transport = await self.create_transport(
            room_id, connection.peer_id, TransportDirection.RECV
        )
        transport_id = transport.id
        consumer_ids: List[str] = []

        sfu_producers = list(connection.producers.values())

        # Consume each existing audio producer
        for producer in sfu_producers:
            if producer.kind == MediaKind.AUDIO:
                try:
                    consumer = await self.consume(
                        room_id,
                        connection.peer_id,
                        transport_id,
                        producer.id,
                        connection.rtp_capabilities or {},
                    )
                    consumer_ids.append(consumer.id)
                except Exception as exc:
                    logger.debug(
                        f"Could not consume producer {producer.id} for recording: {exc}"
                    )

        self._recordings[room_id] = {
            "recording_id": recording_id,
            "output_dir": str(safe_output_dir),
            "transport_id": transport_id,
            "consumer_ids": consumer_ids,
            "recorder_peer_id": connection.peer_id,
            "file_count": len(consumer_ids),
            "acoustic_defense": self._acoustic_defense_to_dict(),
        }
        logger.info(
            f"Recording started for room {room_id} (id={recording_id}, "
            f"consumers={len(consumer_ids)})"
        )
        return {"recording_id": recording_id, "file_count": len(consumer_ids)}

    async def stop_recording(self, room_id: str) -> Optional[List[str]]:
        """Stop recording, tear down recorder media, and return file paths."""
        _, ensure_within, _ = _recording_path_guards()
        info = self._recordings.get(room_id)
        if info is None:
            return None

        # The recorder uses a real recv transport and one consumer per audio
        # producer. Removing only the local metadata leaks both resources on
        # mediasoup. Tear them down before calculating the output paths; each
        # request is best-effort so one stale consumer cannot prevent cleanup
        # of the remaining transport.
        teardown_complete = True
        recorder_peer_id = info.get("recorder_peer_id")
        if recorder_peer_id:
            key = self._get_connection_key(room_id, str(recorder_peer_id))
            connection = self._connections.get(key)
            if connection is None:
                teardown_complete = False
            else:
                failed_consumers: List[str] = []
                for consumer_id in list(info.get("consumer_ids") or []):
                    consumer_id = str(consumer_id)
                    try:
                        await self._request(
                            connection,
                            "closeConsumer",
                            {"consumerId": consumer_id},
                        )
                    except Exception as exc:
                        teardown_complete = False
                        failed_consumers.append(consumer_id)
                        logger.debug(
                            "Failed to close recorder consumer %s in room %s: %s",
                            consumer_id,
                            room_id,
                            exc,
                        )
                    else:
                        # Only remove local state after the SFU confirms the
                        # close. Failed resources remain retryable.
                        connection.consumers.pop(consumer_id, None)
                info["consumer_ids"] = failed_consumers

                transport_id = info.get("transport_id")
                if transport_id:
                    transport_id = str(transport_id)
                    try:
                        await self._request(
                            connection,
                            "closeWebRtcTransport",
                            {"transportId": transport_id},
                        )
                    except Exception as exc:
                        teardown_complete = False
                        logger.debug(
                            "Failed to close recorder transport %s in room %s: %s",
                            transport_id,
                            room_id,
                            exc,
                        )
                    else:
                        # A successfully closed transport must not be retried.
                        info["transport_id"] = None
                        if connection.recv_transport_id == transport_id:
                            connection.recv_transport_id = None

        filepaths: List[str] = []
        for idx in range(info.get("file_count", 0)):
            candidate = os.path.join(
                str(
                    ensure_within(
                        os.path.join(
                            info["output_dir"],
                            f"{info['recording_id']}_track_{idx}.webm",
                        ),
                        info["output_dir"],
                    )
                ),
            )
            if os.path.isfile(candidate):
                filepaths.append(candidate)

        if not filepaths:
            fallback = str(
                ensure_within(
                    os.path.join(info["output_dir"], f"{info['recording_id']}.webm"),
                    info["output_dir"],
                )
            )
            filepaths.append(fallback)

        if teardown_complete:
            self._recordings.pop(room_id, None)
        else:
            # Keep the metadata so shutdown or a later lifecycle callback can
            # retry releasing resources instead of treating partial cleanup as
            # complete.
            logger.warning(
                "Recording media teardown incomplete for room %s; retained for retry",
                room_id,
            )
        logger.info(f"Recording stopped for room {room_id}, files={len(filepaths)}")
        return filepaths

    def _record_recording_teardown_failure(self, room_id: str, reason: str) -> None:
        """Record unrecoverable recorder cleanup after a connection is closed."""
        info = self._recordings.pop(room_id, None)
        if info is None:
            return
        failures = getattr(self, "_recording_teardown_failures", None)
        if failures is None:
            failures = self._recording_teardown_failures = {}
        failures[room_id] = {
            "recording_id": info.get("recording_id"),
            "consumer_ids": list(info.get("consumer_ids") or []),
            "transport_id": info.get("transport_id"),
            "reason": reason,
            "failed_at": time.time(),
        }
        logger.error(
            "Recorder teardown for room %s was incomplete at shutdown: %s",
            room_id,
            reason,
        )
