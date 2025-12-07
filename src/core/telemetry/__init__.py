"""
Telemetry module - Collects anonymized response time data from clients.

This module provides:
- Storage for client-submitted response time telemetry
- Aggregation and statistics for admin dashboards
- Rate limiting to prevent abuse

Usage:
    # In main.py (setup once)
    from src.core import telemetry
    telemetry.setup(db)
    
    # In any other file
    from src.core import telemetry
    
    telemetry.submit_response_times(user_id, data)
    stats = telemetry.get_endpoint_stats()
"""

from typing import Optional, List, Dict, Any
from dataclasses import dataclass
import time

_db = None
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


def setup(db) -> None:
    """
    Initialize the telemetry module.
    
    Args:
        db: Database instance (must be connected)
    """
    global _db, _setup_complete
    
    _db = db
    _create_tables()
    _setup_complete = True


def _create_tables() -> None:
    """Create telemetry tables if they don't exist."""
    global _db
    
    schema = """
    CREATE TABLE IF NOT EXISTS telemetry_response_times (
        id INTEGER PRIMARY KEY,
        endpoint TEXT NOT NULL,
        method TEXT NOT NULL,
        response_time_ms REAL NOT NULL,
        status_code INTEGER NOT NULL,
        timestamp INTEGER NOT NULL,
        client_id TEXT
    )
    """
    _db.execute(_db.convert_schema(schema) if hasattr(_db, 'convert_schema') else schema)
    
    # Create indexes for efficient querying
    _db.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_endpoint ON telemetry_response_times(endpoint)")
    _db.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_timestamp ON telemetry_response_times(timestamp)")
    _db.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_method_endpoint ON telemetry_response_times(method, endpoint)")


def is_setup() -> bool:
    """Check if telemetry module is initialized."""
    return _setup_complete


def _get_db():
    """Get database instance, ensuring setup was called."""
    if not _setup_complete:
        raise RuntimeError("Telemetry not initialized. Call telemetry.setup(db) first.")
    return _db


def _normalize_endpoint(endpoint: str) -> str:
    """
    Normalize endpoint by replacing numeric IDs and emojis with placeholders.
    
    This groups endpoints like /channels/123/messages and /channels/456/messages
    into a single /channels/:id/messages entry for better aggregation.
    """
    import re
    import urllib.parse
    
    # First decode any URL-encoded characters for consistent handling
    try:
        endpoint = urllib.parse.unquote(endpoint)
    except Exception:
        pass
    
    # Remove query parameters for cleaner grouping
    if '?' in endpoint:
        endpoint = endpoint.split('?')[0]
    
    # Replace numeric IDs (snowflake IDs are typically 15-20 digits)
    normalized = re.sub(r'/(\d{10,20})(?=/|$)', r'/:id', endpoint)
    # Also handle shorter numeric IDs
    normalized = re.sub(r'/(\d+)(?=/|$)', r'/:id', normalized)
    
    # Normalize emoji paths in reactions endpoints
    if '/reactions/' in normalized:
        normalized = re.sub(r'/reactions/[^/]+', '/reactions/:emoji', normalized)
    
    # Normalize invite codes
    if '/invites/' in normalized:
        normalized = re.sub(r'/invites/[a-zA-Z0-9]+', '/invites/:code', normalized)
    
    # Normalize settings keys
    if '/settings/' in normalized:
        normalized = re.sub(r'/settings/[^/]+$', '/settings/:key', normalized)
    
    # Normalize media attachment paths (UUIDs/hashes)
    if '/media/' in normalized:
        normalized = re.sub(r'/media/attachments/[a-f0-9]+\.[a-z]+', '/media/attachments/:file', normalized)
        normalized = re.sub(r'/media/avatars/[a-f0-9]+\.[a-z]+', '/media/avatars/:file', normalized)
        normalized = re.sub(r'/media/icons/[a-f0-9]+\.[a-z]+', '/media/icons/:file', normalized)
    
    # Normalize user search queries
    if '/users/search' in normalized:
        normalized = '/users/search'
    
    return normalized


def submit_response_times(
    entries: List[Dict[str, Any]],
    client_id: Optional[str] = None
) -> int:
    """
    Submit response time measurements from a client.
    
    Args:
        entries: List of response time entries with keys:
            - endpoint: API endpoint path
            - method: HTTP method
            - response_time_ms: Response time in milliseconds
            - status_code: HTTP status code
            - timestamp: Unix timestamp in milliseconds
        client_id: Optional anonymized client identifier
        
    Returns:
        Number of entries successfully stored
    """
    db = _get_db()
    
    now = int(time.time() * 1000)
    batch_data = []
    
    # Pre-import snowflake generator
    try:
        from src.utils.encryption import generate_snowflake_id
        use_snowflake = True
    except ImportError:
        use_snowflake = False
    
    for idx, entry in enumerate(entries):
        try:
            # Validate entry and normalize endpoint
            raw_endpoint = str(entry.get("endpoint", ""))[:255]
            endpoint = _normalize_endpoint(raw_endpoint)
            method = str(entry.get("method", "GET"))[:10].upper()
            response_time_ms = float(entry.get("response_time_ms", 0))
            status_code = int(entry.get("status_code", 0))
            timestamp = int(entry.get("timestamp", now))
            
            # Skip invalid entries
            if not endpoint or response_time_ms < 0 or status_code < 100:
                continue
            
            # Don't accept timestamps too far in the future or past (1 hour window)
            if abs(timestamp - now) > 3600000:
                timestamp = now
            
            # Generate ID
            if use_snowflake:
                entry_id = generate_snowflake_id()
            else:
                entry_id = int(time.time() * 1000000) + idx
            
            batch_data.append((entry_id, endpoint, method, response_time_ms, status_code, timestamp, client_id))
            
        except Exception:
            continue
    
    if not batch_data:
        return 0
    
    # Batch insert for better performance
    try:
        db.executemany(
            """INSERT INTO telemetry_response_times 
               (id, endpoint, method, response_time_ms, status_code, timestamp, client_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            batch_data
        )
        return len(batch_data)
    except AttributeError:
        # Fallback to individual inserts if executemany not available
        stored = 0
        for data in batch_data:
            try:
                db.execute(
                    """INSERT INTO telemetry_response_times 
                       (id, endpoint, method, response_time_ms, status_code, timestamp, client_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    data
                )
                stored += 1
            except Exception:
                continue
        return stored


def get_endpoint_stats(
    hours: int = 24,
    endpoint_filter: Optional[str] = None,
    aggregate_by_pattern: bool = True,
    client_id_filter: Optional[str] = None
) -> List[EndpointStats]:
    """
    Get aggregated statistics for endpoints.
    
    Args:
        hours: Number of hours to look back
        endpoint_filter: Optional endpoint pattern to filter by
        aggregate_by_pattern: If True, aggregate endpoints with IDs into patterns
        client_id_filter: Optional client_id to filter by
        
    Returns:
        List of EndpointStats for each endpoint/method combination
    """
    db = _get_db()
    
    cutoff = int((time.time() - hours * 3600) * 1000)
    
    # Build query based on filters
    query = "SELECT DISTINCT endpoint, method FROM telemetry_response_times WHERE timestamp > ?"
    params: list = [cutoff]
    
    if endpoint_filter:
        query += " AND endpoint LIKE ?"
        params.append(f"%{endpoint_filter}%")
    
    if client_id_filter:
        query += " AND client_id = ?"
        params.append(client_id_filter)
    
    rows = db.fetch_all(query, tuple(params))
    
    # If aggregating by pattern, group endpoints that differ only by IDs
    if aggregate_by_pattern:
        pattern_groups = {}
        for row in rows:
            endpoint = row["endpoint"] if isinstance(row, dict) else row[0]
            method = row["method"] if isinstance(row, dict) else row[1]
            pattern = _normalize_endpoint(endpoint)
            key = (pattern, method)
            if key not in pattern_groups:
                pattern_groups[key] = []
            pattern_groups[key].append(endpoint)
    else:
        pattern_groups = {}
        for row in rows:
            endpoint = row["endpoint"] if isinstance(row, dict) else row[0]
            method = row["method"] if isinstance(row, dict) else row[1]
            pattern_groups[(endpoint, method)] = [endpoint]
    
    stats = []
    for (pattern, method), endpoints in pattern_groups.items():
        all_times = []
        error_count = 0
        
        for endpoint in endpoints:
            times_query = """SELECT response_time_ms, status_code FROM telemetry_response_times 
                   WHERE endpoint = ? AND method = ? AND timestamp > ?"""
            times_params: list = [endpoint, method, cutoff]
            
            if client_id_filter:
                times_query += " AND client_id = ?"
                times_params.append(client_id_filter)
            
            times_rows = db.fetch_all(times_query, tuple(times_params))
            
            for r in times_rows:
                rt = r["response_time_ms"] if isinstance(r, dict) else r[0]
                sc = r["status_code"] if isinstance(r, dict) else r[1]
                all_times.append(rt)
                if sc >= 400:
                    error_count += 1
        
        if not all_times:
            continue
        
        count = len(all_times)
        sorted_times = sorted(all_times)
        p50_idx = int(count * 0.50)
        p95_idx = int(count * 0.95)
        p99_idx = int(count * 0.99)
        
        stats.append(EndpointStats(
            endpoint=pattern,
            method=method,
            count=count,
            avg_response_time_ms=sum(all_times) / count,
            min_response_time_ms=min(all_times),
            max_response_time_ms=max(all_times),
            p50_response_time_ms=sorted_times[min(p50_idx, count - 1)],
            p95_response_time_ms=sorted_times[min(p95_idx, count - 1)],
            p99_response_time_ms=sorted_times[min(p99_idx, count - 1)],
            error_rate=error_count / count if count > 0 else 0,
            last_updated=int(time.time() * 1000)
        ))
    
    stats.sort(key=lambda s: s.count, reverse=True)
    return stats


def get_response_time_history(
    endpoint: str,
    method: str = "GET",
    hours: int = 24,
    bucket_minutes: int = 5
) -> List[Dict[str, Any]]:
    """
    Get response time history bucketed by time intervals.
    
    Args:
        endpoint: API endpoint path
        method: HTTP method
        hours: Number of hours to look back
        bucket_minutes: Size of time buckets in minutes
        
    Returns:
        List of time buckets with avg response time
    """
    db = _get_db()
    
    cutoff = int((time.time() - hours * 3600) * 1000)
    bucket_ms = bucket_minutes * 60 * 1000
    
    rows = db.fetch_all(
        """SELECT timestamp, response_time_ms FROM telemetry_response_times 
           WHERE endpoint = ? AND method = ? AND timestamp > ?
           ORDER BY timestamp""",
        (endpoint, method, cutoff)
    )
    
    if not rows:
        return []
    
    buckets = {}
    for row in rows:
        ts = row["timestamp"] if isinstance(row, dict) else row[0]
        rt = row["response_time_ms"] if isinstance(row, dict) else row[1]
        
        bucket_key = (ts // bucket_ms) * bucket_ms
        if bucket_key not in buckets:
            buckets[bucket_key] = []
        buckets[bucket_key].append(rt)
    
    result = []
    for bucket_ts, times in sorted(buckets.items()):
        result.append({
            "timestamp": bucket_ts,
            "avg_response_time_ms": sum(times) / len(times),
            "count": len(times),
            "min_response_time_ms": min(times),
            "max_response_time_ms": max(times)
        })
    
    return result


def cleanup_old_data(days: int = 30) -> int:
    """
    Remove telemetry data older than specified days.
    
    Args:
        days: Number of days to keep
        
    Returns:
        Number of rows deleted
    """
    db = _get_db()
    
    cutoff = int((time.time() - days * 24 * 3600) * 1000)
    
    cursor = db.execute(
        "DELETE FROM telemetry_response_times WHERE timestamp < ?",
        (cutoff,)
    )
    
    return cursor.rowcount if hasattr(cursor, 'rowcount') else 0


__all__ = [
    'setup',
    'is_setup',
    'submit_response_times',
    'get_endpoint_stats',
    'get_response_time_history',
    'cleanup_old_data',
    'ResponseTimeEntry',
    'EndpointStats',
]
