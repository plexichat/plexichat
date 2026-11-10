"""Quality/stats helpers for the aiortc SFU adapter."""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..quality import parse_quality_metrics
from .constants import _CLOCK_RATES, _DEFAULT_CLOCK_RATE


def _as_number(value: Any) -> Optional[float]:
    """Coerce an aiortc stat field to a finite number, or None.

    aiortc exposes counts/timers as ints or floats and occasionally as
    objects that stringify badly; the coercion is deliberately permissive so
    one odd field never aborts the whole quality sample.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _quality_metrics_from_stats(records: Any) -> Dict[str, Any]:
    """Turn an aiortc ``getStats()`` report into canonical quality metrics.

    Prefers ``remote-inbound-rtp`` (the sender's view of what this peer
    received, which carries ``roundTripTime`` and the loss the encoder
    observed) and falls back to ``inbound-rtp``. aiortc reports ``jitter``
    in seconds and the RTCP RTT in seconds; the canonical schema wants
    milliseconds, so both are scaled. ``packetLoss`` is derived as
    ``lost / (received + lost) * 100`` across the direction's records.

    Returns an empty dict when no usable RTP counters are present, so the
    caller can skip a peer rather than emit a fabricated score.
    """
    buckets = {
        "remote-inbound-rtp": {
            "seen": False,
            "lost": 0,
            "received": 0,
            "jitter_ms": None,
            "rtt_ms": None,
        },
        "inbound-rtp": {
            "seen": False,
            "lost": 0,
            "received": 0,
            "jitter_ms": None,
            "rtt_ms": None,
        },
    }
    for record in records or ():
        stat_type = getattr(record, "type", None)
        if stat_type not in buckets:
            continue
        bucket = buckets[stat_type]
        bucket["seen"] = True
        lost = _as_number(getattr(record, "packetsLost", None))
        received = _as_number(getattr(record, "packetsReceived", None))
        if lost is not None and lost > 0:
            bucket["lost"] += int(lost)
        if received is not None and received > 0:
            bucket["received"] += int(received)

        kind = str(getattr(record, "kind", "") or "").lower()
        clock = _CLOCK_RATES.get(kind, _DEFAULT_CLOCK_RATE)
        jitter = _as_number(getattr(record, "jitter", None))
        if jitter is not None and jitter >= 0:
            jitter_ms = jitter * 1000.0 / clock
            current = bucket["jitter_ms"]
            bucket["jitter_ms"] = (
                jitter_ms if current is None else max(current, jitter_ms)
            )
        rtt = _as_number(getattr(record, "roundTripTime", None))
        if rtt is not None and rtt >= 0:
            rtt_ms = rtt * 1000.0
            current_rtt = bucket["rtt_ms"]
            bucket["rtt_ms"] = (
                rtt_ms if current_rtt is None else max(current_rtt, rtt_ms)
            )

    remote = buckets["remote-inbound-rtp"]
    bucket = remote if remote["seen"] else buckets["inbound-rtp"]
    if not bucket["seen"]:
        return {}

    raw: Dict[str, Any] = {}
    total = bucket["received"] + bucket["lost"]
    if total > 0:
        raw["packetLoss"] = bucket["lost"] * 100.0 / total
    if bucket["jitter_ms"] is not None:
        raw["jitter"] = bucket["jitter_ms"]
    if bucket["rtt_ms"] is not None:
        raw["roundTripTime"] = bucket["rtt_ms"]
    return parse_quality_metrics(raw)


__all__ = ["_as_number", "_quality_metrics_from_stats"]
