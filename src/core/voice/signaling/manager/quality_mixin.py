"""Quality handling mixin for SignalingManager."""

import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple

import utils.logger as logger

from ..exceptions import NotConnectedError
from ..models import ConnectionQuality, QualityLevel


class BandwidthEstimator:
    """Periodically scans quality data and adapts simulcast layers."""

    def __init__(
        self,
        quality_data: Dict[Tuple[int, int], Dict],
        sfu_adapter: Any,
        check_interval: float = 5.0,
    ):
        self._quality_data = quality_data
        self._sfu = sfu_adapter
        self._check_interval = check_interval
        self._degraded: Dict[Tuple[int, int], bool] = {}
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._check_interval)
                now = time.time()
                for key, metrics in list(self._quality_data.items()):
                    if now - metrics.get("_ts", now) > 30:
                        continue
                    channel_id_str, peer_id = key
                    packet_loss = metrics.get("packet_loss", 0) or 0
                    jitter = metrics.get("jitter", 0) or 0
                    degraded = packet_loss > 5.0 or jitter > 100.0
                    was_degraded = self._degraded.get(key, False)
                    if degraded and not was_degraded:
                        logger.info(
                            f"BandwidthEstimator: degrading channel_id={channel_id_str}, "
                            f"peer_id={peer_id} (loss={packet_loss}%, jitter={jitter}ms)"
                        )
                        try:
                            await self._sfu.set_preferred_layers(
                                f"voice_{channel_id_str}",
                                str(peer_id),
                                "",
                                0,
                                0,
                            )
                        except Exception as exc:
                            logger.debug(
                                f"BandwidthEstimator: set_preferred_layers failed: {exc}"
                            )
                        self._degraded[key] = True
                    elif not degraded and was_degraded:
                        logger.info(
                            f"BandwidthEstimator: restoring channel_id={channel_id_str}, "
                            f"peer_id={peer_id} (loss={packet_loss}%, jitter={jitter}ms)"
                        )
                        try:
                            await self._sfu.set_preferred_layers(
                                f"voice_{channel_id_str}",
                                str(peer_id),
                                "",
                                2,
                                2,
                            )
                        except Exception as exc:
                            logger.debug(
                                f"BandwidthEstimator: restore layers failed: {exc}"
                            )
                        self._degraded[key] = False
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug(f"BandwidthEstimator: scan error: {exc}")


class QualityMixin:
    """Mixin handling quality monitoring methods."""

    _connections: Dict[int, Any]
    _quality_data: Dict[Tuple[int, int], Dict]
    _bandwidth_estimator: Optional[BandwidthEstimator]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._quality_data = {}
        self._bandwidth_estimator = None

    def _get_timestamp(self) -> int: ...

    def _get_sfu(self): ...

    def init_bandwidth_estimator(self) -> None:
        """Start the bandwidth estimator background task."""
        try:
            sfu = self._get_sfu()
            est = BandwidthEstimator(self._quality_data, sfu)
            est.start()
            self._bandwidth_estimator = est
        except Exception as exc:
            logger.debug(f"BandwidthEstimator init skipped: {exc}")

    def shutdown_bandwidth_estimator(self) -> None:
        if self._bandwidth_estimator:
            self._bandwidth_estimator.stop()
            self._bandwidth_estimator = None

    def update_quality_hint(
        self,
        channel_id: Any,
        peer_id: Any,
        target_bitrate: Optional[int] = None,
        quality_level: Optional[str] = None,
        **metrics: Any,
    ) -> bool:
        cid = int(channel_id) if not isinstance(channel_id, int) else channel_id
        pid = int(peer_id) if not isinstance(peer_id, int) else peer_id
        now = int(time.time())
        entry: Dict[str, Any] = {"_ts": now}
        if target_bitrate is not None:
            entry["bitrate"] = target_bitrate
        if quality_level is not None:
            entry["quality_level"] = quality_level
        for k in ("bitrate", "packet_loss", "jitter", "round_trip_time", "score"):
            if k in metrics:
                entry[k] = metrics[k]
        self._quality_data[(cid, pid)] = entry

        if pid in self._connections:
            conn = self._connections[pid]
            conn.last_activity = now

        return True

    def get_connection_quality(
        self, user_id: int, channel_id: int
    ) -> ConnectionQuality:
        connection = self._connections.get(user_id)
        if not connection:
            raise NotConnectedError(
                "User not connected to voice", user_id=user_id, channel_id=channel_id
            )

        key = (channel_id, user_id)
        stored = self._quality_data.get(key)
        if stored:
            return ConnectionQuality(
                user_id=user_id,
                channel_id=channel_id,
                quality_level=QualityLevel.GOOD,
                bitrate=stored.get("bitrate", 64000),
                packet_loss=stored.get("packet_loss", 0.0),
                jitter=stored.get("jitter", 0.0),
                round_trip_time=stored.get("round_trip_time", 50),
                timestamp=self._get_timestamp(),
            )

        return ConnectionQuality(
            user_id=user_id,
            channel_id=channel_id,
            quality_level=QualityLevel.GOOD,
            bitrate=64000,
            packet_loss=0.0,
            jitter=0.0,
            round_trip_time=50,
            timestamp=self._get_timestamp(),
        )

    def get_quality_stats(self, channel_id: int) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for (cid, pid), metrics in self._quality_data.items():
            if cid == channel_id:
                entry: Dict[str, Any] = {
                    "channel_id": cid,
                    "peer_id": pid,
                    "bitrate": metrics.get("bitrate"),
                    "packet_loss": metrics.get("packet_loss"),
                    "jitter": metrics.get("jitter"),
                    "round_trip_time": metrics.get("round_trip_time"),
                    "score": metrics.get("score"),
                }
                results.append(entry)
        return results
