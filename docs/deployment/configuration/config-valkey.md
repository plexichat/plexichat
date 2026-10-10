# Valkey configuration

This guide describes how to configure Valkey for Plexichat in production. Valkey provides caching, session storage, and pub/sub messaging. It improves performance and enables horizontal scaling. Plexichat can run without Valkey, but production deployments should enable it.

## Configuration location

Add Valkey settings to your configuration file under the `valkey` key. Plexichat also accepts `redis` as a legacy alias for backward compatibility (`src/core/database/valkey_client/base.py:45`).

```yaml
valkey:
  # Valkey settings
```

## When to use Valkey

### Enable or disable

```yaml
valkey:
  enabled: false   # Default is false. Set to true for production.
```

### Deployment considerations

**Why Valkey matters**

Valkey is an in-memory data store that provides high-performance caching and real-time features.

**Valkey is required for:**

*   Multi-worker deployments with multiple server processes
*   Horizontal scaling across multiple servers
*   WebSocket gateway with multiple instances
*   Real-time presence and typing indicators at scale

**Valkey is optional for:**

*   Single-server deployments with low traffic
*   Development and testing environments
*   Proof-of-concept deployments

**What happens when Valkey is disabled**

When `enabled` is `false`, Plexichat falls back to in-memory storage:

*   You store session data in process memory. Plexichat loses sessions on restart.
*   You disable caching. Every request hits the database.
*   WebSocket presence works only on a single instance.
*   You cannot scale horizontally.

**Performance impact**

*   With Valkey, caching reduces database queries by 60–80%.
*   Without Valkey, every request hits the database, which limits scalability.
*   A typical Valkey operation adds 1–5 ms of latency, compared with 50–200 ms for a database query.

## Basic settings

```yaml
valkey:
  enabled: false
  host: "localhost"          # From ${VALKEY_HOST:-localhost} in src/config_defaults.py:113
  port: 6379
  password: ""               # From ${VALKEY_PASSWORD:-} in src/config_defaults.py:115
  db: 0
```

### Deployment considerations

**Host**

*   Use `localhost` when Valkey runs on the same machine as Plexichat.
*   Use a remote host for dedicated servers or managed services such as AWS ElastiCache or Google Cloud Memorystore.

**Port**

*   The default is `6379`. Change it only if Valkey listens on a different port.
*   When you enable TLS, managed services often use `6380`.

**Password**

*   Always set a strong password in production.
*   You can leave the password empty for local development.
*   Do not commit passwords to version control. Use environment variables or a secrets manager.

**Database number**

*   The default is `0`. Use a different number (`0`–`15`) to isolate Plexichat from other applications on the same Valkey server.
*   Valkey Cluster does not support multiple databases.

Plexichat reads these values in `src/core/database/valkey_client/base.py:51`. Test connectivity:

```bash
redis-cli -h <host> -p <port> -a <password> ping
```

## TLS settings

Plexichat exposes a single TLS toggle for Valkey.

### Configuration

```yaml
valkey:
  ssl: false   # Default is false. Set to true to use TLS. Defined in src/config_defaults.py:117
```

There are no `ssl_cert_reqs` or `ssl_ca_certs` keys in `src/config_defaults.py:111` or `src/core/database/valkey_client/base.py:61`. When you set `ssl: true`, the client sets `use_tls: true` in the Glide client configuration:

*   `src/core/database/valkey_client/base.py:149` passes `use_tls: self.ssl` to `GlideClientConfiguration`.
*   `src/api/websocket/__init__.py:487` does the same for the cross-worker pub/sub client: `use_tls = valkey_cfg.get("ssl", False)`.

Certificate verification uses the system CA bundle. For self-signed certificates, add the CA to the system trust store on the Plexichat host. There is no per-Valkey `ssl_ca_certs` path.

### Deployment considerations

**When to enable TLS**

Enable TLS whenever Valkey is not on `localhost`, or when your managed service requires it. Valkey traffic includes session tokens and presence data.

**Managed services**

Most managed Valkey and Redis services require TLS:

*   AWS ElastiCache — TLS enabled by default, typically on port `6380`
*   Google Cloud Memorystore — TLS required for production
*   Azure Cache for Redis — TLS recommended, typically on port `6380`

## Connection pool

```yaml
valkey:
  connection_pool:
    max_connections: 50   # Default in src/config_defaults.py:121
    timeout: 5            # Seconds. Converted to milliseconds for Glide.
```

Plexichat applies these values in `src/core/database/valkey_client/base.py:158`. `timeout` becomes `request_timeout = timeout * 1000` and `reconnect_strategy = BackoffStrategy(num_of_retries=3, factor=timeout*1000, exponent_base=2)`. The cross-worker listener uses `request_timeout: 5000` (`src/api/websocket/__init__.py:501`).

**Maximum connections**

*   Default is `50`.
*   Small deployments: `10`–`20` connections.
*   Medium deployments: `20`–`50` connections.
*   Large deployments: `50`–`100` connections.
*   Each worker process maintains its own pool. Calculate total connections as `workers * max_connections`.

**Connection timeout**

*   Default is `5` seconds.
*   Use `5`–`10` seconds for production.
*   Increase to `15`–`20` seconds for geographically distributed deployments.
*   Reduce to `2`–`3` seconds when Valkey runs on `localhost`.

**Sizing guidelines**

| Deployment scale | Workers | Max connections per worker | Total connections |
| --- | --- | --- | --- |
| Development | 1 | 10 | 10 |
| Small (<100 users) | 1–2 | 20 | 20–40 |
| Medium (100–1000 users) | 2–4 | 30 | 60–120 |
| Large (1000+ users) | 4–8 | 50 | 200–400 |

Monitor `connected_clients` with `redis-cli info clients` and ensure the Valkey `maxclients` setting exceeds your total connection budget.

## Key management

```yaml
valkey:
  key_prefix: "plexichat:"   # Default in src/config_defaults.py:118
```

Plexichat prefixes every key with `key_prefix` (`src/core/database/valkey_client/base.py:63`). The helper `src/core/database/valkey_client/base.py:207` adds the prefix if it is missing.

**Guidelines**

*   End the prefix with `:` for readability.
*   Use environment-specific prefixes such as `plexichat-prod:` or `plexichat-staging:` when you share a Valkey server.
*   Use unique prefixes for each Plexichat instance.

List Plexichat keys:

```bash
redis-cli --scan --pattern "plexichat:*"
```

## TTL (time to live)

```yaml
valkey:
  ttl:
    session: 1800    # 30 minutes. Defined in src/config_defaults.py:123
    presence: 300    # 5 minutes
    cache: 60        # 1 minute
```

Plexichat reads these values in `src/core/database/valkey_client/base.py:65`.

**Session TTL**

*   Default is `1800` seconds (30 minutes).
*   Plexichat refreshes the TTL on activity, so active users stay logged in.
*   Reduce to `900` for high-security environments or increase to `3600` for convenience.

**Presence TTL**

*   Default is `300` seconds (5 minutes). This covers brief network interruptions.
*   Reduce to `120` for more accurate presence or increase to `600` to reduce writes.

**Cache TTL**

*   Default is `60` seconds. This balances hit rate and staleness.
*   Shorter TTL gives fresher data. Longer TTL improves cache hit rate.

## Cache limits

```yaml
valkey:
  cache_max_items: 1000   # Default in src/config_defaults.py:124
```

Without a limit, caches can grow without bound. Set `cache_max_items` to bound memory use. Each cached item typically uses 1–10 KB.

Configure Valkey memory policy in `valkey.conf`:

```conf
maxmemory 256mb
maxmemory-policy allkeys-lru
```

Recommended policies:

*   `allkeys-lru` — Evicts the least recently used keys. Recommended for caching.
*   `volatile-lru` — Evicts only keys with a TTL.
*   `allkeys-lfu` — Evicts the least frequently used keys.

## Cross-worker pub/sub

When `valkey.enabled` is `true`, Plexichat starts a cross-worker listener that subscribes to `{key_prefix}events:cross_worker` (`src/api/websocket/__init__.py:344`). The connection uses a dedicated Glide client created in `src/api/websocket/__init__.py:463` so subscribing does not put the shared client into subscriber mode.

You can observe and tune the poll loop via:

*   `cross_worker_listener_status()` — Returns `enabled`, `status`, `backoff_sec`, `poll_count`, and `running` (`src/api/websocket/__init__.py:100`).
*   `get_poll_tuning()` and `update_poll_tuning()` — Read and hot-reload tuning constants (`src/api/websocket/__init__.py:559`).

## Scaling considerations

**Single Valkey instance**

Use a single instance for fewer than 10,000 concurrent users. Enable persistence (RDB and AOF) and consider a replica for high availability.

**Valkey Sentinel**

Use Sentinel when you need automatic failover. Deploy at least three Sentinels. The client configuration in this repository does not expose Sentinel settings — you must wire Sentinel manually if you need it.

**Valkey Cluster**

Use Cluster for more than 100,000 concurrent users or datasets larger than 100 GB. Deploy at least three masters with replicas. Cluster does not support multiple databases.

**Managed Valkey services**

*   AWS ElastiCache for Valkey
*   Google Cloud Memorystore
*   Azure Cache for Redis
*   DigitalOcean Managed Databases

When you use a managed service, use the provided endpoint and port, enable `ssl: true`, and use the service monitoring and failover features.

## Monitoring and maintenance

**Key metrics**

*   `connected_clients` — Active connections
*   `used_memory` and `used_memory_peak` — Memory usage
*   `keyspace_hits` and `keyspace_misses` — Cache hit rate
*   `expired_keys` and `evicted_keys` — Expiration and eviction count

**Health checks**

```bash
# Check connectivity
redis-cli -h <host> -p <port> -a <password> ping

# Check memory
redis-cli -h <host> -p <port> -a <password> info memory

# Check statistics
redis-cli -h <host> -p <port> -a <password> info stats
```

**Maintenance**

*   Enable RDB snapshots for point-in-time recovery.
*   Monitor for unbounded key growth.
*   Test Valkey upgrades before you apply them in production.

## Related documentation

*   [Default configuration reference](../../default-config.md)
*   [Database configuration](config-database.md)
*   [Authentication configuration](config-authentication.md)
*   [Deployment guide](../getting-started.md)
