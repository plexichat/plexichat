"""aiortc stats into canonical quality samples."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import utils.logger as logger

from .models import AiortcPeer
from .runtime import _total_bytes_sent, clock
from .stats import _quality_metrics_from_stats


class QualityBridgeMixin:
    """Quality pulled from ``RTCPeerConnection.getStats()``."""

    _rooms: Dict[str, Dict[str, AiortcPeer]]
    _last_quality_sample: Dict[str, Tuple[int, float]]

    def _bitrate_for(
        self, peer_id: str, bytes_sent: Optional[int], now: float
    ) -> Optional[int]:
        """Egress bitrate in bits/s from the change in the peer's byte counter.

        Returns ``None`` for the first observation of a peer: a rate needs two
        samples, and a fabricated one would drive degradation decisions.
        """
        if bytes_sent is None:
            return None
        previous = self._last_quality_sample.get(peer_id)
        self._last_quality_sample[peer_id] = (bytes_sent, now)
        if previous is None:
            return None
        last_bytes, last_ts = previous
        elapsed = now - last_ts
        delta = bytes_sent - last_bytes
        if elapsed <= 0 or delta < 0:
            return None
        return int(delta * 8.0 / elapsed)

    async def collect_quality(self) -> List[tuple]:
        """Poll each peer's aiortc stats into canonical quality samples.

        Returns ``(room_id, peer_id, metrics)`` with the SFU-scoped ids the
        manager minted, so the base ``report_quality`` can map them back to
        the numeric ``(channel_id, user_id)`` the quality store is keyed by.
        Peers whose pc reports nothing measurable are skipped.
        """
        samples: List[tuple] = []
        now = clock().monotonic()
        for room_id, peers in list(self._rooms.items()):
            for peer_id, peer in list(peers.items()):
                try:
                    records = await peer.pc.getStats()
                except Exception as exc:
                    logger.debug(
                        f"aiortc quality poll failed for peer {peer_id} "
                        f"in room {room_id}: {exc}"
                    )
                    continue
                metrics = _quality_metrics_from_stats(records)
                bitrate = self._bitrate_for(peer_id, _total_bytes_sent(records), now)
                if bitrate is not None:
                    metrics["bitrate"] = bitrate
                if metrics:
                    samples.append((room_id, peer_id, metrics))
        return samples
