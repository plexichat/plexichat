"""Shared bridge between SFU-side quality observations and the quality store.

``QualityMixin`` (the signaling manager) keeps ``_quality_data`` keyed by the
*canonical* numeric ``(channel_id, user_id)`` pair, and ``BandwidthEstimator``
reads that map to decide when to degrade simulcast layers.

Every adapter speaks in SFU-scoped identifiers instead: the signaling manager
mints rooms as ``voice_<channel_id>`` (``manager/base.py``) and peers as
``user_<user_id>`` (``manager/sdp_mixin.py``). Adapters that can observe
peer quality therefore need the same two things:

1. a translation from those identifiers back to the canonical key, and
2. a way to hand the sample to the mixin without ever letting a metric update
   raise into a media-path coroutine.

``canonical_channel_id`` / ``canonical_user_id`` / ``parse_quality_metrics``
cover (1); ``QualityReporter`` covers (2) for request/response backends that
have no server push and must poll for stats.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Iterable, List, Optional, Tuple

import utils.logger as logger

# Identifier formats minted by the signaling manager (see module docstring).
SFU_ROOM_PREFIX = "voice_"
SFU_PEER_PREFIX = "user_"

# Default cadence for pull-based quality collection. The estimator itself
# scans on a similar interval, so polling faster only adds SFU load.
DEFAULT_QUALITY_POLL_INTERVAL_S = 5.0

# SFU stat field names -> canonical ``update_quality_hint`` keyword names.
# Covers mediasoup (``packetLoss`` / ``roundTripTime``) and the snake_case
# spellings used by other stat producers.
_METRIC_ALIASES = {
    "bitrate": "bitrate",
    "target_bitrate": "bitrate",
    "packetloss": "packet_loss",
    "packet_loss": "packet_loss",
    "jitter": "jitter",
    "roundtriptime": "round_trip_time",
    "round_trip_time": "round_trip_time",
    "rtt": "round_trip_time",
    "score": "score",
}


def canonical_channel_id(room_id: Any) -> Optional[int]:
    """Map an SFU room id (``voice_<channel_id>``) to the channel id."""
    if isinstance(room_id, bool):
        return None
    if isinstance(room_id, int):
        return room_id if room_id > 0 else None
    text = str(room_id or "")
    if not text.startswith(SFU_ROOM_PREFIX):
        return None
    try:
        value = int(text[len(SFU_ROOM_PREFIX) :])
    except ValueError:
        return None
    return value if value > 0 else None


def canonical_user_id(peer_id: Any) -> Optional[int]:
    """Map an SFU peer id (``user_<user_id>``) to the user id."""
    if isinstance(peer_id, bool):
        return None
    if isinstance(peer_id, int):
        return peer_id if peer_id > 0 else None
    text = str(peer_id or "")
    if not text.startswith(SFU_PEER_PREFIX):
        return None
    try:
        value = int(text[len(SFU_PEER_PREFIX) :])
    except ValueError:
        return None
    return value if value > 0 else None


def parse_quality_metrics(payload: Any) -> Dict[str, Any]:
    """Normalise SFU stat records into ``update_quality_hint`` keyword metrics.

    ``payload`` may be a single stat mapping or a list of them (mediasoup
    reports one record per stat type); later records win. Unknown fields and
    non-numeric values are dropped rather than guessed -- the quality store
    clamps whatever survives.
    """
    records = payload if isinstance(payload, (list, tuple)) else [payload]
    metrics: Dict[str, Any] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        for key, value in record.items():
            canonical = _METRIC_ALIASES.get(str(key).strip().lower())
            if canonical is None or value is None:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number != number:  # NaN
                continue
            metrics[canonical] = int(number) if number.is_integer() else number
    return metrics


class QualityReporter:
    """Periodically pull SFU-side metrics and forward them to the quality sink.

    Request/response adapters (mediasoup REST, Janus) get no server push, so
    scores can only be polled. The reporter owns the cadence and the error
    containment; the adapter only implements ``collect_quality()`` and
    ``report_quality()``.
    """

    def __init__(
        self, adapter: Any, interval: float = DEFAULT_QUALITY_POLL_INTERVAL_S
    ) -> None:
        self._adapter = adapter
        self._interval = max(1.0, float(interval))
        self._task: Optional[asyncio.Task] = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> bool:
        """Start the poll loop; returns whether a loop was available.

        Mirrors ``BandwidthEstimator.start``: when called without a running
        event loop the reporter stays stopped rather than creating a detached
        loop.
        """
        if self.running:
            return True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("SFU quality reporter start deferred: no running loop")
            return False
        self._task = loop.create_task(self._run())
        return True

    def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._interval)
                await self.poll_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # never let a poll kill the task
                logger.debug(f"SFU quality poll failed: {exc}")

    async def poll_once(self) -> None:
        """Collect one round of samples and hand each to the adapter."""
        collector = getattr(self._adapter, "collect_quality", None)
        if collector is None:
            return
        try:
            samples: Iterable[Tuple[Any, Any, Dict[str, Any]]] = await collector()
        except Exception as exc:
            logger.debug(f"SFU quality collection failed: {exc}")
            return
        for sample in samples or ():
            try:
                room_id, peer_id, metrics = sample
            except (TypeError, ValueError):
                continue
            if not metrics:
                continue
            self._adapter.report_quality(room_id, peer_id, metrics)


__all__: List[str] = [
    "DEFAULT_QUALITY_POLL_INTERVAL_S",
    "QualityReporter",
    "SFU_PEER_PREFIX",
    "SFU_ROOM_PREFIX",
    "canonical_channel_id",
    "canonical_user_id",
    "parse_quality_metrics",
]
