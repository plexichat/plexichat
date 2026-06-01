# Performance Tuning

This guide covers performance optimization for Plexichat deployments. It identifies performance-sensitive subsystems, provides configuration recommendations for different scales, and explains the trade-offs involved in each tuning decision.

## Performance-Sensitive Subsystems

### Message Creation and Event Fanout

Message creation triggers several operations: content encryption (when enabled), database writes, search indexing, and real-time fanout to all online members via WebSocket. This is the highest-throughput path in the system.

**Configuration Impact**

```yaml
messaging:
  encrypt_messages: true        # Encryption adds ~0.5-2ms per message
  max_message_length: 4000      # Longer messages take more to encrypt and store
  max_attachments_per_message: 10 # Attachment processing is I/O-heavy

search:
  write_time_indexing: true     # Indexing adds a write per message
```

**Tuning Recommendations**

- **High-throughput (>100 msg/sec)**: Consider `write_time_indexing: false` and rely on batch reindexing. Disable `encrypt_messages` only if your threat model permits.
- **Standard (<50 msg/sec)**: Keep defaults. Encryption and indexing overhead is negligible at this scale.

---

### WebSocket Gateway

The gateway maintains persistent connections for all online users. Each connection consumes memory for the connection state, heartbeat tracking, and optionally compression context.

**Configuration Impact**

```yaml
websocket:
  heartbeat_interval_ms: 45000     # More frequent = more overhead
  max_connections_per_user: 5       # More connections = more memory
  compression_enabled: true         # Compression saves bandwidth but costs CPU/memory
  max_message_size: 65536           # Larger limits = larger buffers
  max_decompressed_size: 262144     # Must hold decompressed data in memory
```

**Tuning Recommendations**

- Scale: <500 users | Connections: <2,500 | Heartbeat: 45s | Compression: Enabled | Workers: 1
- Scale: 500-2,000 | Connections: <10,000 | Heartbeat: 60s | Compression: Enabled | Workers: 2
- Scale: 2,000-10,000 | Connections: <50,000 | Heartbeat: 90s | Compression: Evaluate | Workers: 4
- Scale: 10,000+ | Connections: 50,000+ | Heartbeat: 90s | Compression: Disabled | Workers: 4+

- **Compression memory**: Each compressed connection uses 64-256KB. At 10,000 connections, that's 0.6-2.5GB. Disable compression if memory-constrained.
- **Heartbeat frequency**: Lower frequency reduces per-connection overhead but delays detection of dead connections.

---

### Search and Indexing

Search performance depends on the backend choice and index size.

**Configuration Impact**

```yaml
search:
  backend: "sqlite_fts5"      # FTS5 is the only built-in backend
  result_limit: 100            # Larger limits = slower queries
  batch_size: 100              # Larger batches = faster reindexing, more memory
  write_time_indexing: true    # Immediate indexing vs batch
```

**Tuning Recommendations**

- **SQLite FTS5**: Appropriate up to ~1M messages. Beyond that, queries may slow down on broad searches.
- **Result limit**: Reduce to 50 if broad queries are slow. Increase to 200 only if users need deep pagination.
- **Batch size**: Increase to 500-1000 during initial indexing of large databases, then reduce to 100 for steady-state.
- **Write-time indexing**: Disable under very high write throughput (>100 msg/sec) to reduce write amplification.

---

### Media Processing

Media uploads involve virus scanning (if enabled), image optimization, thumbnail generation, perceptual hashing, and deduplication checks.

**Configuration Impact**

```yaml
media:
  scanner_enabled: false       # ClamAV scanning adds significant latency
  image_optimize: true         # Optimization reduces storage but costs CPU
  phash:
    enabled: true              # Perceptual hashing adds processing time
  deduplication:
    enabled: true              # Dedup checks add I/O per upload
  image_processing:
    max_dimension: 16384       # Very large images are slow to process
    max_pixels: 178956970      # Pixel limit prevents memory exhaustion
  video_processing:
    ffprobe_timeout: 30        # Video metadata extraction timeout
```

**Tuning Recommendations**

- **Virus scanning**: Only enable if you have a dedicated ClamAV instance. Scanning adds 1-10 seconds per file depending on size.
- **Image optimization**: Keep enabled for most deployments. The CPU cost is amortized by reduced storage and bandwidth.
- **Phash/dedup**: Keep enabled for community platforms where duplicate content is common. Disable for small, private deployments where it's unnecessary overhead.
- **Very large images**: Reduce `max_dimension` to 8192 and `max_pixels` to 44739242 to prevent memory spikes from extremely large uploads.

---

### Database Performance

**Configuration Impact**

```yaml
database:
  type: "sqlite"              # SQLite for small, PostgreSQL for large
  connection_pool:
    min_connections: 2
    max_connections: 20
    connect_timeout: 10
  monitoring:
    slow_query_threshold_ms: 1000
    alert_on_slow_queries: true
```

**SQLite Tuning**

- SQLite is appropriate for deployments up to ~100 concurrent users. It's zero-configuration and fast for read-heavy workloads.
- **Write concurrency**: SQLite has limited write concurrency (one writer at a time). If you see write contention, switch to PostgreSQL.
- **WAL mode**: Plexichat uses WAL (Write-Ahead Logging) by default for better read concurrency.

**PostgreSQL Tuning**

- For 100+ concurrent users, switch to PostgreSQL. It handles concurrent writes efficiently.
- Set `connection_pool.max_connections` to 20-50 depending on your PostgreSQL `max_connections` setting.
- **Never** set `max_connections` higher than PostgreSQL's limit minus a safety margin for administrative connections.

See [Database Configuration](deployment/configuration/config-database.md) for detailed setup guidance.

---

### Redis Caching

**Configuration Impact**

```yaml
redis:
  enabled: false              # Enable for multi-worker or high-scale
  connection_pool:
    max_connections: 50
    timeout: 5
  ttl:
    session: 1800
    presence: 300
    cache: 60
```

**When to Enable Redis**

- Single worker, <100 users: No
- Multiple workers: Yes (shared state)
- >500 concurrent users: Recommended
- Session persistence needed: Yes
- Rate limit accuracy needed: Yes

Without Redis, session and rate-limit state is stored in-process memory. This works for single-worker deployments but is lost on restart and cannot be shared across workers.

See [Redis Configuration](deployment/configuration/config-redis.md) for detailed setup guidance.

---

### Configuration Hot-Path Performance

`utils.config.get()` is one of the most frequently called functions in the
codebase — it backs nearly every module's config lookup. Three small changes
keep it off the critical path.

**Caching `get()` results**

`ConfigLoader.get()` now caches its return value keyed on `(key, repr(default))`.
Subsequent calls for the same key/default return the cached value in O(1) without
touching the underlying dict. The cache is invalidated automatically on:

- `set()` (after a successful save)
- `_load_config()` (after a fresh load or reload)

A module-level sentinel is used to distinguish a cached `None` from a cache
miss, so keys that legitimately map to `None` are not re-queried forever.

**Atomic config writes**

`_save_config()` previously opened the destination file for writing directly.
A crash mid-write left a truncated, unparseable YAML/JSON file behind. It now
writes to `<config_path>.tmp` and uses `os.replace()` to rename over the real
path. On any error the temporary file is cleaned up and the original config
is preserved. The rename is atomic on POSIX and on NTFS, so readers always
see either the old or the new file in full.

**Skipping regex when no `$` is present**

`${VAR}` interpolation is a rare case in practice — most config strings are
plain literals. The resolver used to run the interpolation regex over every
string in the config tree, paying the cost of a regex compile-cache lookup
and an empty match attempt. There is now an early return in both
`_resolve_value()` and `_resolve_config()` when the string contains no `$`
character, skipping the regex entirely.

**Deep-copy `get_all()`**

`config.get_all()` previously returned a shallow copy of the live config dict.
Nested structures (lists, dicts) were still shared with the loader, so a
caller mutating them could corrupt the in-process config. The returned dict
is now a `copy.deepcopy()` of the loader's state, so it is safe to pass
through serialization or templating layers without defensive copying.

---

### Database Query Cache

`fetch_one()` and `fetch_all()` cache their results under a stable cache key
(`f"{one|all}:{query}:{params}"`) with a per-row TTL (default 1 second).
Repeated lookups for the same query within the TTL hit the cache and never
touch the database.

**Backed by an `OrderedDict` LRU**

The cache used to be a plain `dict` that was rebuilt (O(n) comprehension)
every time the 100-entry cap was hit. It is now an `OrderedDict` capped at
100 entries. Reads call `move_to_end(key)` so the most recently used entry
moves to the back; writes evict from the front with `popitem(last=False)`.
Eviction is O(1) per entry, and the cache size is bounded without rebuilding
the whole structure.

**Invalidation**

The cache is cleared in three places:

- Any `INSERT` / `UPDATE` / `DELETE` / `REPLACE` / `DROP` / `CREATE` / `ALTER`
  statement (the `execute()` method detects these by inspecting the
  upper-cased query prefix and calls `.clear()` under the cache lock).
- `begin_transaction()`, `commit()`, and `rollback()` — transactions should
  never read from a snapshot taken before the transaction boundary.
- The TTL expires (lazy expiry on read).

**Retry classification**

`execute()` and `execute_many()` previously treated every
`sqlite3.OperationalError` (or any error with "closed" in the message) as a
connection error and retried. This caused harmless errors like
`cannot commit - no transaction is active` to be retried needlessly. The
check is now more precise:

- A regex-anchored substring check looks for actual transport phrases
  (`connection refused`, `connection reset`, `connection closed`,
  `no connection`, `disconnected`, `broken pipe`, `lost connection`).
- `InterfaceError` is always retried.
- `DatabaseError` is retried only when the message suggests disconnection.
- `OperationalError` is retried only when the message contains a
  connection-failure phrase — not by type name alone.

In addition, the schema-error guard now also matches `no such index` and
`no such view`, so transient errors that look like schema problems are not
retried.

---

## Server Worker Configuration

```yaml
server:
  workers: 1
```

**Tuning Recommendations**

- Scale: <100 users | Workers: 1 | Redis: No | Notes: Simplest setup
- Scale: 100-500 users | Workers: 1-2 | Redis: Recommended | Notes: Redis for session sharing
- Scale: 500-2,000 users | Workers: 2-4 | Redis: Required | Notes: Multiple workers must share state
- Scale: 2,000+ users | Workers: 4+ | Redis: Required | Notes: Add workers for CPU-bound workloads

**Important**: Multiple workers require Redis for shared session state, rate limit counters, and presence tracking. Without Redis, each worker maintains independent state, leading to inconsistent behavior.

---

## Monitoring and Health Endpoints

Use these endpoints for lightweight health checks without consuming application resources:

- ``GET /health`` (Quick readiness signal): Minimal
- ``GET /api/v1/status`` (Availability and maintenance state): Low
- ``GET /api/v1/version`` (Compatibility and version checks): Minimal
- ``GET /api/v1/media/upload/sessions`` (In-progress upload visibility): Low
- ``GET /api/v1/media/compression/status`` (Media-processing status): Low

**Monitoring Configuration**

```yaml
monitoring:
  enabled: true
  log_interval: 300
  metrics_enabled: true
  alert_thresholds:
    cpu_percent: 80
    memory_percent: 85
    db_pool_saturation_percent: 75
    query_time_ms: 5000
    api_response_time_ms: 2000
```

Configure alert thresholds based on your deployment's baseline performance. The default thresholds are conservative starting points.

---

## Logging Overhead

```yaml
logging:
  level: "DEBUG"        # DEBUG in dev, INFO/WARNING in production
  max_bytes: 10485760   # 10MB per file
  backup_count: 5       # 5 rotated files = 50MB total
  zip_logs: true        # Compress rotated files
  rotate: true          # Enable rotation
  include_exception_details: false  # SECURITY: disable in production
```

**Production Recommendations**

- Set `level: "INFO"` or `"WARNING"` - DEBUG logging is very verbose and impacts I/O performance.
- `include_exception_details: false` - Exception details in logs can reveal implementation information and increase log volume.
- Keep rotation enabled to prevent unbounded log growth.

---

## Complete Performance-Oriented Production Config

```yaml
server:
  host: "127.0.0.1"
  port: 8000
  workers: 2
  reload: false

logging:
  level: "INFO"
  rotate: true
  include_exception_details: false

database:
  type: "postgres"
  connection_pool:
    min_connections: 5
    max_connections: 25

redis:
  enabled: true
  connection_pool:
    max_connections: 50

websocket:
  heartbeat_interval_ms: 60000
  compression_enabled: true
  max_message_size: 65536

search:
  backend: "sqlite_fts5"
  write_time_indexing: true
  result_limit: 100

messaging:
  encrypt_messages: true

media:
  scanner_enabled: false
  image_optimize: true

api:
  debug: false

monitoring:
  enabled: true
  metrics_enabled: true
```

---

## Related Documentation

- [Database Configuration](deployment/configuration/config-database.md) - PostgreSQL/SQLite setup and connection pooling
- [Redis Configuration](deployment/configuration/config-redis.md) - Caching, session storage, and scaling
- [WebSocket Configuration](deployment/configuration/config-websocket.md) - Gateway tuning for concurrent connections
- [Search Configuration](deployment/configuration/config-search.md) - Search backend and indexing performance
- [Rate Limiting Configuration](deployment/configuration/config-rate-limiting.md) - Rate limit tuning
- [Default Configuration Reference](default-config.md) - Complete configuration reference
