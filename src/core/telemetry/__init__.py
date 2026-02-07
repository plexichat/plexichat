"""
Telemetry module - Collects anonymized response time data from clients.
"""

from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass
import time
import logging

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
    _create_tables()
    _setup_complete = True


def _create_tables() -> None:
    """Create telemetry tables."""
    if _db is None:
        raise RuntimeError("Telemetry database not set")
    db: Any = _db
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
    db.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_endpoint ON telemetry_response_times(endpoint)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_timestamp ON telemetry_response_times(timestamp)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_method_endpoint ON telemetry_response_times(method, endpoint)")


def is_setup() -> bool:
    return _setup_complete


def _get_db():
    if not _setup_complete:
        raise RuntimeError("Telemetry not initialized.")
    return _db


def submit_response_times(entries: List[Dict[str, Any]], client_id: Optional[str] = None) -> int:
    db = _get_db()
    now = int(time.time() * 1000)
    batch_data = []
    
    try:
        from src.utils.encryption import generate_snowflake_id
        gen_id = generate_snowflake_id
    except ImportError:
        gen_id = lambda: int(time.time() * 1000000)

    for entry in entries:
        try:
            batch_data.append((
                gen_id(),
                str(entry.get("endpoint", ""))[:255],
                str(entry.get("method", "GET"))[:10].upper(),
                float(entry.get("response_time_ms", 0)),
                int(entry.get("status_code", 0)),
                int(entry.get("timestamp", now)),
                client_id,
                int(entry.get("db_queries", 0)),
                float(entry.get("db_time_ms", 0.0))
            ))
        except Exception: continue

    if not batch_data: return 0
    db.execute_many(
        """INSERT INTO telemetry_response_times 
           (id, endpoint, method, response_time_ms, status_code, timestamp, client_id, db_queries, db_time_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        batch_data
    )
    return len(batch_data)


def get_endpoint_stats(hours: int = 24, endpoint_filter: Optional[str] = None, client_id_filter: Optional[str] = None) -> List[EndpointStats]:
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
    params = [cutoff]
    if endpoint_filter:
        query += " AND endpoint LIKE ?"
        params.append(f"%{endpoint_filter}%")
    if client_id_filter:
        query += " AND client_id = ?"
        params.append(client_id_filter)
        
    query += " GROUP BY endpoint, method ORDER BY count DESC"
    
    rows = db.fetch_all(query, tuple(params))
    stats = []
    now = int(time.time() * 1000)
    
    for r in rows:
        stats.append(EndpointStats(
            endpoint=r["endpoint"], method=r["method"], count=r["count"],
            avg_response_time_ms=r["avg_ms"], min_response_time_ms=r["min_ms"], max_response_time_ms=r["max_ms"],
            p50_response_time_ms=r["avg_ms"], p95_response_time_ms=r["avg_ms"] * 1.2, p99_response_time_ms=r["max_ms"], # Approximations for speed
            error_rate=r["err_rate"], last_updated=now,
            avg_queries=r["avg_q"], avg_query_time_ms=r["avg_dt"],
            error_count=int(r["err_count"])
        ))
    return stats


def get_response_time_history(endpoint: str, method: str = "GET", hours: int = 24, bucket_minutes: int = 5) -> List[Dict[str, Any]]:
    db = _get_db()
    cutoff = int((time.time() - hours * 3600) * 1000)
    bucket_ms = bucket_minutes * 60 * 1000
    
    # Bucket by timestamp using floor division in SQL if possible, or post-process
    rows = db.fetch_all(
        "SELECT timestamp, response_time_ms FROM telemetry_response_times WHERE endpoint = ? AND method = ? AND timestamp > ? ORDER BY timestamp",
        (endpoint, method, cutoff)
    )
    if not rows: return []
    buckets = {}
    for r in rows:
        bk = (r["timestamp"] // bucket_ms) * bucket_ms
        if bk not in buckets: buckets[bk] = []
        buckets[bk].append(r["response_time_ms"])
    
    return [{"timestamp": k, "avg_response_time_ms": sum(v)/len(v), "count": len(v)} for k, v in sorted(buckets.items())]


def reset_all_stats() -> int:
    db = _get_db()
    db.execute("DELETE FROM telemetry_response_times")
    return 0

__all__ = ["setup", "is_setup", "submit_response_times", "get_endpoint_stats", "get_response_time_history", "reset_all_stats", "EndpointStats"]