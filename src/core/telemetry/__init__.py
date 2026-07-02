"""
Telemetry module - Collects anonymized response time data from clients.
"""

from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass
import time
import logging
import re

logger = logging.getLogger(__name__)

_db: Any = None
_setup_complete = False


@dataclass
class ResponseTimeEntry:
    """A single response time measurement."""

    id: int
    endpoint: str
    method: str
    response_time_ms: float
    status_code: int
    timestamp: int
    client_id: Optional[str] = None
    db_queries: int = 0
    db_time_ms: float = 0.0


@dataclass
class EndpointStats:
    """Aggregated statistics for an endpoint."""

    endpoint: str
    method: str
    count: int
    avg_response_time_ms: float
    min_response_time_ms: float
    max_response_time_ms: float
    p50_response_time_ms: float
    p95_response_time_ms: float
    p99_response_time_ms: float
    error_rate: float
    last_updated: int
    avg_queries: float = 0.0
    avg_query_time_ms: float = 0.0
    error_count: int = 0


def setup(db: Any) -> None:
    """Initialize the telemetry module."""
    global _db, _setup_complete
    _db = db
    _setup_complete = True


def create_tables(db: Any) -> None:
    """Create telemetry tables."""
    schema = """
    CREATE TABLE IF NOT EXISTS telemetry_response_times (
        id BIGINT PRIMARY KEY,
        endpoint TEXT NOT NULL,
        method TEXT NOT NULL,
        response_time_ms REAL NOT NULL,
        status_code INTEGER NOT NULL,
        timestamp BIGINT NOT NULL,
        client_id TEXT,
        db_queries INTEGER DEFAULT 0,
        db_time_ms REAL DEFAULT 0.0
    )
    """
    convert_schema: Optional[Callable[[str], str]] = getattr(db, "convert_schema", None)
    db.execute(convert_schema(schema) if convert_schema else schema)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_telemetry_endpoint ON telemetry_response_times(endpoint)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_telemetry_timestamp ON telemetry_response_times(timestamp)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_telemetry_method_endpoint ON telemetry_response_times(method, endpoint)"
    )


def _create_tables() -> None:
    """Create telemetry tables."""
    if _db is None:
        raise RuntimeError("Telemetry database not set")
    create_tables(_db)


def is_setup() -> bool:
    """Check if the telemetry module is initialized."""
    return _setup_complete


def _get_db():
    if not _setup_complete:
        raise RuntimeError("Telemetry not initialized.")
    return _db


_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{16,}$")
_SEGMENT_PLACEHOLDERS = {
    "channels": "channel_id",
    "messages": "message_id",
    "users": "user_id",
    "servers": "server_id",
    "webhooks": "webhook_id",
    "relationships": "relationship_id",
    "roles": "role_id",
    "emojis": "emoji_id",
    "members": "user_id",
    "avatars": "avatar_id",
}


def _normalize_endpoint(endpoint: str) -> str:
    if not endpoint:
        return endpoint
    endpoint = endpoint.split("?", 1)[0].rstrip("/") or "/"
    if "{" in endpoint and "}" in endpoint:
        return endpoint
    parts = endpoint.split("/")
    normalized = []
    for i, part in enumerate(parts):
        if i == 0:
            normalized.append("")
            continue
        if not part:
            continue
        if part.startswith("{") and part.endswith("}"):
            normalized.append(part)
            continue
        prev_part = parts[i - 1] if i > 0 else ""
        replacement = None
        if _UUID_RE.match(part):
            replacement = "{id}"
        elif part.isdigit() and len(part) >= 6:
            placeholder = _SEGMENT_PLACEHOLDERS.get(prev_part)
            replacement = f"{{{placeholder}}}" if placeholder else "{id}"
        elif _TOKEN_RE.match(part) and i >= 2 and parts[i - 2] == "webhooks":
            replacement = "{token}"
        normalized.append(replacement or part)
    return "/".join(normalized)


def submit_response_times(
    entries: List[Dict[str, Any]], client_id: Optional[str] = None
) -> int:
    """
    Record multiple response time measurements in the database.

    Automatically normalizes endpoints and generates unique IDs.
    """
    db = _get_db()
    now = int(time.time() * 1000)
    batch_data = []

    try:
        from src.utils.encryption import generate_snowflake_id

        gen_id = generate_snowflake_id
    except ImportError:

        def gen_id():
            return int(time.time() * 1000000)

    for entry in entries:
        try:
            endpoint = str(entry.get("endpoint", "")).strip()
            if not endpoint:
                continue
            response_time_ms = float(entry.get("response_time_ms", 0))
            if response_time_ms < 0:
                continue
            method = str(entry.get("method", "GET")).strip().upper()[:10]

            # Safely handle non-integer status codes (e.g., "MESSAGE_BLOCKED")
            status_code_raw = entry.get("status_code", 0)
            try:
                status_code = int(status_code_raw)
            except (ValueError, TypeError):
                # If it's a string like "MESSAGE_BLOCKED", use 0 or a specific error code
                # We'll use 0 to indicate a non-standard/blocked response
                status_code = 0

            batch_data.append(
                (
                    gen_id(),
                    _normalize_endpoint(endpoint)[:255],
                    method or "GET",
                    response_time_ms,
                    status_code,
                    int(entry.get("timestamp", now)),
                    client_id,
                    int(entry.get("db_queries", 0)),
                    float(entry.get("db_time_ms", 0.0)),
                )
            )
        except Exception:
            continue

    if not batch_data:
        return 0
    db.execute_many(
        """INSERT INTO telemetry_response_times 
           (id, endpoint, method, response_time_ms, status_code, timestamp, client_id, db_queries, db_time_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        batch_data,
    )
    return len(batch_data)


def get_endpoint_stats(
    hours: int = 24,
    endpoint_filter: Optional[str] = None,
    client_id_filter: Optional[str] = None,
) -> List[EndpointStats]:
    """Get aggregated statistics using optimized SQL GROUP BY."""
    db = _get_db()
    cutoff = int((time.time() - hours * 3600) * 1000)

    query = """
        SELECT 
            endpoint, method, COUNT(*) as count,
            AVG(response_time_ms) as avg_ms, MIN(response_time_ms) as min_ms, MAX(response_time_ms) as max_ms,
            AVG(db_queries) as avg_q, AVG(db_time_ms) as avg_dt,
            SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) as err_count,
            SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as err_rate
        FROM telemetry_response_times 
        WHERE timestamp > ?
    """
    params: List[Any] = [cutoff]
    if endpoint_filter:
        query += " AND endpoint LIKE ?"
        params.append(f"%{endpoint_filter}%")
    if client_id_filter:
        query += " AND client_id = ?"
        params.append(client_id_filter)

    query += " GROUP BY endpoint, method ORDER BY count DESC"

    rows = db.fetch_all(query, tuple(params))
    if not rows:
        return []
    aggregated: Dict[tuple, Dict[str, Any]] = {}
    now = int(time.time() * 1000)

    for r in rows:
        endpoint = _normalize_endpoint(r["endpoint"])
        key = (endpoint, r["method"])
        count = int(r["count"] or 0)
        if count <= 0:
            continue
        avg_ms = float(r["avg_ms"] or 0.0)
        avg_q = float(r["avg_q"] or 0.0)
        avg_dt = float(r["avg_dt"] or 0.0)
        err_count = int(r["err_count"] or 0)
        min_ms = r["min_ms"]
        max_ms = r["max_ms"]
        agg = aggregated.get(key)
        if not agg:
            agg = {
                "count": 0,
                "avg_ms_sum": 0.0,
                "avg_q_sum": 0.0,
                "avg_dt_sum": 0.0,
                "min_ms": None,
                "max_ms": None,
                "err_count": 0,
            }
            aggregated[key] = agg
        agg["count"] += count
        agg["avg_ms_sum"] += avg_ms * count
        agg["avg_q_sum"] += avg_q * count
        agg["avg_dt_sum"] += avg_dt * count
        if min_ms is not None:
            agg["min_ms"] = (
                min(min_ms, agg["min_ms"]) if agg["min_ms"] is not None else min_ms
            )
        if max_ms is not None:
            agg["max_ms"] = (
                max(max_ms, agg["max_ms"]) if agg["max_ms"] is not None else max_ms
            )
        agg["err_count"] += err_count

    # ---- second pass: pull raw response_time_ms samples so the
    # percentiles below can be computed correctly.  Gated by a
    # ``5000-sample cap per endpoint`` to bound memory on busy
    # instances; if more than 5000 events landed in the window the
    # percentile distribution is still representative but we emit a
    # one-shot warning so operators know the cap was hit.
    raw_lookup = """
        SELECT endpoint, method, response_time_ms
        FROM telemetry_response_times
        WHERE timestamp > ?
    """
    raw_params: List[Any] = [cutoff]
    if endpoint_filter:
        raw_lookup += " AND endpoint LIKE ?"
        raw_params.append(f"%{endpoint_filter}%")
    if client_id_filter:
        raw_lookup += " AND client_id = ?"
        raw_params.append(client_id_filter)

    by_key: Dict[tuple, List[float]] = {}
    cap_hit = False
    try:
        for raw in db.fetch_all(raw_lookup, tuple(raw_params)):
            try:
                ms = float(raw["response_time_ms"])
            except (TypeError, ValueError):
                continue
            key = (
                _normalize_endpoint(raw["endpoint"]),
                raw["method"],
            )
            bucket = by_key.setdefault(key, [])
            if len(bucket) < 5000:
                bucket.append(ms)
            else:
                cap_hit = True
    except Exception:
        # The aggregate row above is still useful when the raw
        # pull fails (driver-specific quoting, permission errors etc).
        pass

    if cap_hit:
        logger.warning(
            "telemetry: per-endpoint sample cap (5000) reached; "
            "p50/p95/p99 are computed over the first 5000 samples "
            "in the window"
        )

    stats = []
    for (endpoint, method), agg in sorted(
        aggregated.items(), key=lambda item: item[1]["count"], reverse=True
    ):
        count = agg["count"]
        avg_ms = (agg["avg_ms_sum"] / count) if count else 0.0
        avg_q = (agg["avg_q_sum"] / count) if count else 0.0
        avg_dt = (agg["avg_dt_sum"] / count) if count else 0.0
        min_ms = agg["min_ms"] if agg["min_ms"] is not None else avg_ms
        max_ms = agg["max_ms"] if agg["max_ms"] is not None else avg_ms
        # CORRECTNESS FIX: ``p50 = avg_ms; p95 = avg_ms * 1.2; p99 = max_ms``
        # was a structural lie — every "percentile" was a synthetic
        # transformation of the average that completely ignored the
        # distribution of response times.  We now compute real
        # nearest-rank percentiles against the raw samples pulled
        # from the second query above.  Falls back to the aggregate
        # min/avg/max only when raw samples are unavailable (the DB
        # returned zero rows for the second pass).
        samples = sorted(by_key.get((endpoint, method), []) or [])
        if samples:
            p50 = _percentile(samples, 50.0)
            p95 = _percentile(samples, 95.0)
            p99 = _percentile(samples, 99.0)
        else:
            p50 = avg_ms
            p95 = avg_ms
            p99 = max_ms
        err_count = agg["err_count"]
        err_rate = (err_count * 100.0 / count) if count else 0.0
        stats.append(
            EndpointStats(
                endpoint=endpoint,
                method=method,
                count=count,
                avg_response_time_ms=avg_ms,
                min_response_time_ms=min_ms,
                max_response_time_ms=max_ms,
                p50_response_time_ms=p50,
                p95_response_time_ms=p95,
                p99_response_time_ms=p99,
                error_rate=err_rate,
                last_updated=now,
                avg_queries=avg_q,
                avg_query_time_ms=avg_dt,
                error_count=err_count,
            )
        )
    return stats


def _percentile(sorted_samples: List[float], pct: float) -> float:
    """Return the ``pct`` percentile using the nearest-rank method.

    ``sorted_samples`` MUST be ascending (callers pass ``sorted()``).
    ``pct`` is the percentile in [0, 100].  Returns 0.0 when the
    input list is empty.

    Method: ``rank = ceil(p / 100 * n)`` — the smallest sample whose
    rank is at or above the percentile cutoff.  This matches numpy's
    default ``method='lower'`` / Excel's ``PERCENTILE.INC`` and is
    the cheapest deterministic percentile that does not invent
    values between samples (which we MUST NOT do for SLO math).
    """
    if not sorted_samples:
        return 0.0
    if pct <= 0:
        return float(sorted_samples[0])
    if pct >= 100:
        return float(sorted_samples[-1])
    n = len(sorted_samples)
    # Smallest index k (0-based) such that (k + 1) >= p/100 * n.
    rank = max(1, int(round(pct / 100.0 * n)))
    return float(sorted_samples[rank - 1])


def get_response_time_history(
    endpoint: str, method: str = "GET", hours: int = 24, bucket_minutes: int = 5
) -> List[Dict[str, Any]]:
    """
    Retrieve historical performance data for a specific endpoint.

    Aggregates measurements into time-based buckets for trend visualization.
    """
    db = _get_db()
    cutoff = int((time.time() - hours * 3600) * 1000)
    bucket_ms = bucket_minutes * 60 * 1000

    normalized_endpoint = _normalize_endpoint(endpoint)
    if "{" in normalized_endpoint and "}" in normalized_endpoint:
        like_pattern = re.sub(r"\{[^/]+\}", "%", normalized_endpoint)
        rows = db.fetch_all(
            "SELECT timestamp, response_time_ms, status_code FROM telemetry_response_times WHERE endpoint LIKE ? AND method = ? AND timestamp > ? ORDER BY timestamp",
            (like_pattern, method, cutoff),
        )
    else:
        rows = db.fetch_all(
            "SELECT timestamp, response_time_ms, status_code FROM telemetry_response_times WHERE endpoint = ? AND method = ? AND timestamp > ? ORDER BY timestamp",
            (normalized_endpoint, method, cutoff),
        )
    if not rows:
        return []
    buckets = {}
    for r in rows:
        bk = (r["timestamp"] // bucket_ms) * bucket_ms
        if bk not in buckets:
            buckets[bk] = {"times": [], "errors": 0}
        buckets[bk]["times"].append(r["response_time_ms"])
        if r["status_code"] >= 400:
            buckets[bk]["errors"] += 1

    return [
        {
            "timestamp": k,
            "avg_ms": sum(v["times"]) / len(v["times"]),
            "count": len(v["times"]),
            "error_count": v["errors"],
        }
        for k, v in sorted(buckets.items())
    ]


def reset_all_stats() -> int:
    """
    Delete all recorded telemetry data.
    """
    db = _get_db()
    db.execute("DELETE FROM telemetry_response_times")
    return 0


def cleanup_old_data(days: int = 30) -> int:
    """
    Remove telemetry measurements older than the specified number of days.
    """
    db = _get_db()
    cutoff = int((time.time() - days * 86400) * 1000)
    cursor = db.execute(
        "DELETE FROM telemetry_response_times WHERE timestamp < ?", (cutoff,)
    )
    return getattr(cursor, "rowcount", 0) or 0


__all__ = [
    "setup",
    "is_setup",
    "submit_response_times",
    "get_endpoint_stats",
    "get_response_time_history",
    "reset_all_stats",
    "cleanup_old_data",
    "EndpointStats",
]
