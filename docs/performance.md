# Performance tuning

This guide helps you tune Plexichat for your deployment size. It identifies performance-sensitive subsystems, shows the configuration that affects each one, and explains the trade-offs.

## Before you begin

All settings in this guide live in `config.yaml`. Defaults come from `src/config_defaults.py:48`. Change one subsystem at a time and measure the result before tuning another.

## Message creation and event fanout

Message creation is the highest-throughput path in the system. Each message triggers content encryption (when enabled), a database write, search indexing, and WebSocket fanout to online members.

### Configuration

```yaml
messaging:
  encrypt_messages: true        # Encryption adds ~0.5–2 ms per message
  max_message_length: 4000      # Longer messages cost more to encrypt and store
  max_attachments_per_message: 10 # Attachment processing is I/O heavy

search:
  write_time_indexing: true     # Indexing adds a write per message
```

### Recommendations

*   **High throughput (>100 messages per second)** — Set `write_time_indexing: false` and rely on batch reindexing. Disable `encrypt_messages` only if your threat model allows it.
*   **Standard (<50 messages per second)** — Keep the defaults. Encryption and indexing overhead is negligible at this scale.

## WebSocket gateway

The gateway holds a persistent connection for each online user. Each connection uses memory for state, heartbeat tracking, and optionally compression.

### Configuration

```yaml
websocket:
  heartbeat_interval_ms: 45000     # More frequent heartbeats add overhead
  max_connections_per_user: 5       # More connections use more memory
  compression_enabled: true         # Compression saves bandwidth but costs CPU and memory
  max_message_size: 65536           # Larger limits need larger buffers
  max_decompressed_size: 262144     # Decompressed data stays in memory
```

### Recommendations

| Scale | Connections | Heartbeat | Compression | Workers |
|---|---|---|---|---|
| <500 users | <2,500 | 45s | Enabled | 1 |
| 500–2,000 users | <10,000 | 60s | Enabled | 2 |
| 2,000–10,000 users | <50,000 | 90s | Evaluate | 4 |
| 10,000+ users | 50,000+ | 90s | Disabled | 4+ |

*   **Compression memory** — Each compressed connection uses 64–256 KB. At 10,000 connections, that is 0.6–2.5 GB. Disable compression if you are memory-constrained.
*   **Heartbeat frequency** — Lower frequency reduces per-connection overhead but delays detection of dead connections.

## Search and indexing

Search performance depends on the backend and index size.

### Configuration

```yaml
search:
  backend: "sqlite_fts5"      # The only built-in backend
  result_limit: 100            # Larger limits slow queries
  batch_size: 100              # Larger batches speed reindexing but use more memory
  write_time_indexing: true    # Immediate indexing versus batch
```

### Recommendations

*   **SQLite FTS5** — Suitable for up to ~1M messages. Beyond that, broad searches can slow down.
*   **Result limit** — Reduce to `50` if broad queries are slow. Increase to `200` only when users need deep pagination.
*   **Batch size** — Use `500–1000` during initial indexing of large databases, then return to `100`.
*   **Write-time indexing** — Disable when write throughput exceeds ~100 messages per second to reduce write amplification.

## Media processing

Media uploads can trigger virus scanning, image optimization, thumbnail generation, perceptual hashing, and deduplication.

### Configuration

```yaml
media:
  scanner_enabled: false       # ClamAV scanning adds significant latency
  image_optimize: true         # Optimization saves storage but costs CPU
  phash:
    enabled: true              # Perceptual hashing adds processing time
  deduplication:
    enabled: true              # Deduplication checks add I/O per upload
  image_processing:
    max_dimension: 16384       # Very large images are slow to process
    max_pixels: 178956970      # Pixel limit prevents memory exhaustion
  video_processing:
    ffprobe_timeout: 30        # Video metadata extraction timeout
```

### Recommendations

*   **Virus scanning** — Enable only when you have a dedicated ClamAV instance. Scanning adds 1–10 seconds per file.
*   **Image optimization** — Keep enabled for most deployments. The CPU cost is offset by reduced storage and bandwidth.
*   **Perceptual hash and deduplication** — Keep enabled for community platforms where duplicates are common. Disable for small, private deployments to reduce overhead.
*   **Very large images** — To prevent memory spikes, reduce `max_dimension` to `8192` and `max_pixels` to `44739242`.

## Database performance

### Configuration

```yaml
database:
  type: "sqlite"              # Use SQLite for small deployments, PostgreSQL for large
  connection_pool:
    min_connections: 2
    max_connections: 20
    connect_timeout: 10
  monitoring:
    slow_query_threshold_ms: 1000
    alert_on_slow_queries: true
```

### SQLite

SQLite is suitable for up to ~100 concurrent users. It is zero-configuration and fast for read-heavy workloads.

*   **Write concurrency** — SQLite allows one writer at a time. If you see write contention, switch to PostgreSQL.
*   **WAL mode** — Plexichat uses WAL (Write-Ahead Logging) by default for better read concurrency. See `src/core/database/engines/sqlite.py`.

### PostgreSQL

For more than 100 concurrent users, use PostgreSQL.

*   Set `connection_pool.max_connections` to `20–50` based on your PostgreSQL `max_connections`.
*   Never set `max_connections` higher than the PostgreSQL limit minus a safety margin for administrative connections.

For details, see [Database configuration](deployment/configuration/config-database.md).

## Valkey caching

### Configuration

```yaml
valkey:
  enabled: false              # Enable for multi-worker or high-scale deployments
  connection_pool:
    max_connections: 50
    timeout: 5
  ttl:
    session: 1800
    presence: 300
    cache: 60
```

### When to enable Valkey

| Condition | Enable Valkey |
|---|---|
| Single worker, <100 users | No |
| Multiple workers | Yes — shared state is required |
| >500 concurrent users | Recommended |
| Session persistence needed | Yes |
| Rate-limit accuracy needed | Yes |

Without Valkey, session and rate-limit state lives in process memory. That works for single-worker deployments but does not survive restarts and cannot be shared across workers.

For details, see [Valkey configuration](deployment/configuration/config-valkey.md).

## Configuration hot path

`utils.config.get()` backs nearly every module's config lookup. Three small changes keep it off the critical path.

### Cache `get()` results

`ConfigLoader.get()` caches return values keyed on `(key, repr(default))`. Later calls for the same key return the cached value in O(1) without touching the underlying dict. The cache is invalidated on `set()` and on `_load_config()`. A sentinel distinguishes a cached `None` from a cache miss.

### Atomic config writes

`_save_config()` writes to `<config_path>.tmp` and renames with `os.replace()`. If the process crashes mid-write, the original file remains intact. The rename is atomic on POSIX and NTFS.

### Skip regex when there is no `$`

`${VAR}` interpolation is rare. Before running the interpolation regex, `_resolve_value()` and `_resolve_config()` return early when the string contains no `$`, which avoids a regex compile-cache lookup and an empty match.

### Deep-copy `get_all()`

`config.get_all()` returns `copy.deepcopy()` of the loader state. Callers can mutate the result without corrupting in-process config.

## Database query cache

`fetch_one()` and `fetch_all()` cache results under `f"{one|all}:{query}:{params}"` with a 1-second TTL (default). Repeated lookups within the TTL hit the cache and avoid a database round trip.

### OrderedDict LRU

The cache is an `OrderedDict` capped at 100 entries. Reads call `move_to_end(key)` and writes evict from the front with `popitem(last=False)`. Eviction is O(1) and the size stays bounded without rebuilding the structure.

### Invalidation

The cache is cleared in three places:

*   Any `INSERT`, `UPDATE`, `DELETE`, `REPLACE`, `DROP`, `CREATE`, or `ALTER` statement (`execute()` detects the upper-cased prefix and clears under the cache lock).
*   `begin_transaction()`, `commit()`, and `rollback()` — transactions never read from a snapshot taken before the boundary.
*   TTL expiry (lazy expiry on read).

### Retry classification

`execute()` and `execute_many()` retry only when the error indicates a connection failure:

*   A substring check looks for transport phrases such as `connection refused`, `connection reset`, `connection closed`, `no connection`, `disconnected`, `broken pipe`, and `lost connection`.
*   `InterfaceError` is always retried.
*   `DatabaseError` is retried only when the message suggests disconnection.
*   `OperationalError` is retried only when the message contains a connection-failure phrase, not by type alone.

The schema-error guard also matches `no such index` and `no such view`.

## Server workers

```yaml
server:
  workers: 1
```

| Scale | Workers | Valkey | Notes |
|---|---|---|---|
| <100 users | 1 | No | Simplest setup |
| 100–500 users | 1–2 | Recommended | Valkey for session sharing |
| 500–2,000 users | 2–4 | Required | Multiple workers must share state |
| 2,000+ users | 4+ | Required | Add workers for CPU-bound workloads |

Multiple workers require Valkey for shared session state, rate-limit counters, and presence tracking. Without Valkey, each worker keeps independent state, which leads to inconsistent behavior.

## Monitoring and health endpoints

Use these endpoints for lightweight health checks:

*   `GET /health` — Quick readiness signal. Cost: minimal.
*   `GET /api/v1/status` — Availability and maintenance state. Cost: low.
*   `GET /api/v1/version` — Compatibility and version checks. Cost: minimal.
*   `GET /api/v1/media/upload/sessions` — In-progress upload visibility. Cost: low.
*   `GET /api/v1/media/compression/status` — Media-processing status. Cost: low.

### Monitoring configuration

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

Set alert thresholds from your deployment's baseline. The defaults are conservative starting points.

## Logging overhead

```yaml
logging:
  level: "DEBUG"        # Use DEBUG in development, INFO or WARNING in production
  max_bytes: 10485760   # 10 MB per file
  backup_count: 5       # 5 rotated files = 50 MB total
  zip_logs: true        # Compress rotated files
  rotate: true          # Enable rotation
  include_exception_details: false  # Disable in production
```

In production:

*   Set `level` to `INFO` or `WARNING`. `DEBUG` is verbose and increases I/O.
*   Keep `include_exception_details` set to `false`. Exception details can reveal implementation details and increase log volume.
*   Keep rotation enabled to prevent unbounded log growth.

## Production configuration example

Use this as a starting point for a production deployment and adjust for your scale.

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

valkey:
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

## Related documentation

*   [Database configuration](deployment/configuration/config-database.md) — PostgreSQL and SQLite setup and connection pooling.
*   [Valkey configuration](deployment/configuration/config-valkey.md) — Caching, session storage, and scaling.
*   [WebSocket configuration](deployment/configuration/config-websocket.md) — Gateway tuning for concurrent connections.
*   [Search configuration](deployment/configuration/config-search.md) — Search backend and indexing performance.
*   [Rate limiting configuration](deployment/configuration/config-rate-limiting.md) — Rate-limit tuning.
*   [Default configuration reference](default-config.md) — Complete configuration reference.
