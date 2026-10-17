"""Quality handling mixin for SignalingManager."""

import asyncio
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import utils.logger as logger

from ..exceptions import NotConnectedError
from ..models import (
    ConnectionQuality,
    QualityLevel,
    QUALITY_BITRATE_THRESHOLDS,
)


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
        if self._task is not None and not self._task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("BandwidthEstimator start deferred: no running event loop")
            return
        self._task = loop.create_task(self._run())

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
                    # H6: prune stale entries instead of skipping them so the
                    # shared dict (and the mirror ``_degraded`` map) cannot
                    # grow unboundedly for peers that stopped reporting.
                    if now - metrics.get("_ts", now) > 30:
                        self._quality_data.pop(key, None)
                        self._degraded.pop(key, None)
                        continue
                    channel_id, peer_id = key
                    channel_id_str = str(channel_id)
                    packet_loss = metrics.get("packet_loss", 0) or 0
                    jitter = metrics.get("jitter", 0) or 0
                    degraded = packet_loss > 5.0 or jitter > 100.0
                    was_degraded = self._degraded.get(key, False)
                    # SFU peers are keyed ``user_<id>`` (see sdp_mixin), so the
                    # adapter's connection map is ``voice_<cid>:user_<id>``.
                    # Passing the bare numeric id used to miss every lookup,
                    # silently disabling simulcast degradation.
                    sfu_peer = f"user_{peer_id}"
                    if degraded and not was_degraded:
                        logger.info(
                            f"BandwidthEstimator: degrading channel_id={channel_id_str}, "
                            f"peer_id={peer_id} (loss={packet_loss}%, jitter={jitter}ms)"
                        )
                        try:
                            await self._sfu.set_preferred_layers(
                                f"voice_{channel_id_str}",
                                sfu_peer,
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
                                sfu_peer,
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

    if TYPE_CHECKING:

        def _get_timestamp(self) -> int: ...

    _connections: Dict[int, Any]
    _quality_data: Dict[Tuple[int, int], Dict]
    _bandwidth_estimator: Optional[BandwidthEstimator]
    _get_sfu: Any

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._quality_data = {}
        self._bandwidth_estimator = None

    def init_bandwidth_estimator(self) -> None:
        """Start the bandwidth estimator background task (idempotent)."""
        if self._bandwidth_estimator is not None:
            return
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
        self.init_bandwidth_estimator()
        cid = int(channel_id) if not isinstance(channel_id, int) else channel_id
        pid = int(peer_id) if not isinstance(peer_id, int) else peer_id

        # Reject hints for peers with no active connection.
        connection = self._connections.get(pid)
        if not connection:
            return False

        # SECURITY/correctness: a quality hint is scoped to the channel the
        # peer's signaling connection is bound to. Without this check a member
        # of channel A can write ``_quality_data[(B, A)]``, so B's
        # ``get_quality_stats`` / ``get_connection_quality`` report forged
        # metrics for a user who is not in that channel. Mirrors the
        # channel-binding guards on ICE candidates and SDP offers.
        bound_channel = getattr(connection, "channel_id", None)
        if bound_channel is None or int(bound_channel) != cid:
            logger.warning(
                f"Channel mismatch on quality hint for user {pid}: "
                f"bound={bound_channel} offered={cid}"
            )
            return False

        now = int(time.time())
        entry: Dict[str, Any] = {"_ts": now}
        if target_bitrate is not None:
            entry["bitrate"] = target_bitrate
        if quality_level is not None:
            try:
                level = QualityLevel(str(quality_level))
            except ValueError:
                level = None
            if level is not None:
                entry["quality_level"] = level
                # Derive a suggested bitrate from the level when the caller
                # did not supply one explicitly.
                if "bitrate" not in entry:
                    thresholds = QUALITY_BITRATE_THRESHOLDS.get(level)
                    if thresholds:
                        entry["bitrate"] = thresholds["max"]

        # M8: client-supplied metrics are UNVALIDATED — negative or absurd
        # floats were accepted verbatim and fed the shared BandwidthEstimator,
        # letting one peer poison degradation decisions for the whole channel.
        # Clamp every numeric metric to a sane, non-negative range; garbage
        # values degrade to a neutral default rather than being stored.
        for k in ("bitrate", "packet_loss", "jitter", "round_trip_time", "score"):
            if k not in metrics:
                continue
            raw = metrics[k]
            try:
                num = float(raw)
            except (TypeError, ValueError):
                continue
            if num != num or num in (float("inf"), float("-inf")):
                continue  # NaN / infinity: drop
            if k == "packet_loss":
                entry[k] = max(0.0, min(num, 100.0))
            elif k == "jitter":
                entry[k] = max(0.0, min(num, 10000.0))
            elif k == "round_trip_time":
                entry[k] = max(0.0, min(num, 600000.0))
            elif k == "score":
                entry[k] = max(0.0, min(num, 100.0))
            else:  # bitrate
                entry[k] = max(0, min(int(num), 100_000_000))
        self._quality_data[(cid, pid)] = entry
        connection.last_activity = now

        return True

    def _compute_quality_level(self, metrics: Dict[str, Any]) -> QualityLevel:
        # Honor an explicit quality level hint when the caller supplied one.
        explicit = metrics.get("quality_level")
        if explicit is not None:
            try:
                return (
                    explicit
                    if isinstance(explicit, QualityLevel)
                    else QualityLevel(str(explicit))
                )
            except ValueError:
                pass  # Invalid hint → fall through to metric-based computation.

        packet_loss = metrics.get("packet_loss", 0) or 0
        jitter = metrics.get("jitter", 0) or 0
        bitrate = metrics.get("bitrate", 64000)
        if packet_loss > 5.0 or jitter > 100.0 or bitrate < 16000:
            return QualityLevel.CRITICAL
        if packet_loss > 2.0 or jitter > 50.0 or bitrate < 32000:
            return QualityLevel.POOR
        if packet_loss > 1.0 or jitter > 20.0 or bitrate < 64000:
            return QualityLevel.FAIR
        if bitrate < 128000:
            return QualityLevel.GOOD
        return QualityLevel.EXCELLENT

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
            quality_level = self._compute_quality_level(stored)
            return ConnectionQuality(
                user_id=user_id,
                channel_id=channel_id,
                quality_level=quality_level,
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
