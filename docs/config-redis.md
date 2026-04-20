# Redis Configuration

This guide covers Redis configuration for deploying Plexichat in production. Redis provides caching, session storage, and pub/sub messaging capabilities that significantly improve performance and enable horizontal scaling.

## Configuration Location

All Redis settings are nested under the `redis` key in your configuration file:

```yaml
redis:
  # All Redis settings go here
```

## When to Use Redis

### Configuration

```yaml
redis:
  enabled: false
```

### Deployment Considerations

**Why Redis Matters**

Redis is an in-memory data store that provides high-performance caching and real-time features. While Plexichat can operate without Redis, enabling it is strongly recommended for production deployments.

**Required For**

- Multi-worker deployments (multiple server processes) - see [Deployment Guide](deployment.md#horizontal-scaling)
- Horizontal scaling across multiple servers
- WebSocket gateway with multiple instances
- Production-grade performance under load
- Real-time features like presence and typing indicators

**Optional For**

- Single-server deployments with low traffic
- Development and testing environments
- Proof-of-concept deployments

**What Happens When Disabled**

When Redis is disabled, Plexichat falls back to in-memory storage:
- Session data is stored in process memory (lost on restart) - see [Authentication Configuration](config-authentication.md#session-management)
- Caching is disabled (all queries hit the database) - see [Database Configuration](config-database.md)
- WebSocket presence is limited to single instance
- Horizontal scaling is not possible

**Performance Impact**

- **With Redis**: Database queries reduced by 60-80% through caching
- **Without Redis**: Every request hits the database, limiting scalability
- **Benchmark**: Redis typically adds 1-5ms latency per operation vs 50-200ms for database queries

---

## Basic Settings

Enable/disable Redis and basic connection settings.

### Configuration

```yaml
redis:
  enabled: false
  host: "localhost"
  port: 6379
  password: ""
  db: 0
```

### Deployment Considerations

**Connection Parameters**

**Host**

- **Localhost**: Appropriate for single-server deployments where Redis runs on the same machine
- **Remote Host**: Use for dedicated Redis servers or managed Redis services (AWS ElastiCache, Google Cloud Memorystore)
- **Cluster Mode**: For Redis Cluster deployments, configure multiple hosts (requires custom setup)

**Port**

- **Default**: 6379 is the standard Redis port
- **Custom**: Change only if your Redis server uses a non-standard port
- **SSL/TLS**: When using SSL, port may differ (typically 6380 for cloud services)

**Password**

- **Production**: Always set a strong password for Redis authentication
- **Development**: Can be empty for local development
- **Security**: Never commit passwords to version control. Use environment variables or secrets management

**Database Number**

- **Default**: 0 is the first Redis database
- **Isolation**: Use different database numbers to separate Plexichat from other applications
- **Limitation**: Redis supports 0-15 (16 databases total). Note that Redis Cluster does not support multiple databases.

**Operational Notes**

- Ensure Redis is running and accessible before starting Plexichat
- Test connectivity with `redis-cli -h <host> -p <port> -a <password> ping`
- Monitor Redis memory usage and eviction policies
- Set appropriate `maxmemory` and `maxmemory-policy` in redis.conf

---

## SSL/TLS Settings

Secure connection settings for Redis.

### Configuration

```yaml
redis:
  ssl: false
  ssl_cert_reqs: "required"
  ssl_ca_certs: ""
```

### Deployment Considerations

**Why SSL Matters**

Redis traffic includes sensitive data like session tokens and user presence information. SSL/TLS encryption protects this data in transit, especially when Redis is on a different server or network.

**SSL Mode**

- **disable**: No encryption (appropriate only for localhost in trusted environments)
- **allow**: Try SSL, accept unencrypted if SSL fails (not recommended for production)
- **optional**: Try SSL, accept unencrypted if server doesn't support SSL (appropriate for development)
- **require**: Require SSL, fail if SSL not available (recommended for production with self-signed certs)
- **verify-ca**: Require SSL and verify CA certificate (recommended for production with known CA)
- **verify-full**: Require SSL, verify CA certificate, and verify hostname (recommended for production with public CA)

**Certificate Requirements**

- **ssl_cert_reqs**: Set to "required" or higher for production
- **ssl_ca_certs**: Path to CA certificate bundle for SSL verification
  - For self-signed certificates: Provide the CA certificate that signed the Redis cert
  - For public CAs: Usually not needed if system CA bundle is trusted
  - For cloud services: Provider typically provides CA certificate path

**Production SSL Recommendations**

- Use SSL for all production deployments where Redis is not on localhost
- Use "verify-full" when using certificates from trusted CAs
- Use "verify-ca" when using self-signed certificates with a known CA
- Ensure your Redis server is configured to accept SSL connections
- Rotate SSL certificates before they expire

**Cloud Service SSL**

Most managed Redis services require SSL:

- **AWS ElastiCache**: SSL enabled by default, port 6380
- **Google Cloud Memorystore**: SSL required for production
- **Azure Cache for Redis**: SSL recommended, port 6380

---

## Connection Pool

Redis connection pool settings.

### Configuration

```yaml
redis:
  connection_pool:
    max_connections: 50
    timeout: 5
```

### Deployment Considerations

**Why Connection Pooling Matters**

Creating new Redis connections is expensive (network round-trip, authentication). Connection pooling reuses existing connections, significantly improving performance and reducing resource usage.

**Maximum Connections**

- **Default**: 50 connections
- **Small Deployment**: 10-20 connections sufficient for low traffic
- **Medium Deployment**: 20-50 connections for moderate traffic
- **Large Deployment**: 50-100 connections for high traffic
- **Multi-Worker**: Each worker process maintains its own pool. Calculate: `workers * max_connections`

**Rationale**: Maximum connections limit resource usage and prevent overwhelming the Redis server. Too few causes connection wait times during traffic spikes. Too many wastes resources.

**Connection Timeout**

- **Default**: 5 seconds
- **Production**: 5-10 seconds is appropriate
- **High-Latency Networks**: Increase to 15-20 seconds for geographically distributed deployments
- **Localhost**: Can reduce to 2-3 seconds for local Redis

**Rationale**: Timeout prevents indefinite waiting when Redis is unavailable or overloaded. Too short causes unnecessary failures during brief load spikes. Too long delays error reporting.

**Pool Sizing Guidelines**

| Deployment Scale | Workers | Max Connections per Worker | Total Connections | Notes |
|-----------------|---------|---------------------------|------------------|-------|
| Development | 1 | 10 | 10 | Single user, minimal load |
| Small (<100 users) | 1-2 | 20 | 20-40 | Low concurrency |
| Medium (100-1000 users) | 2-4 | 30 | 60-120 | Moderate concurrency |
| Large (1000+ users) | 4-8 | 50 | 200-400 | High concurrency |

**Operational Notes**

- Monitor Redis `connected_clients` metric to understand actual usage
- Increase max_connections if you see connection timeout errors
- Ensure Redis `maxclients` configuration is higher than your total connection needs
- Consider Redis Cluster for very high connection scenarios (thousands of connections)

---

## Key Management

Prefix all Redis keys to avoid collisions with other applications.

### Configuration

```yaml
redis:
  key_prefix: "plexichat:"
```

### Deployment Considerations

**Why Key Prefixing Matters**

Key prefixing prevents key collisions when multiple applications share the same Redis server. It also makes it easier to identify and manage Plexichat-specific keys.

**Prefix Selection**

- **Default**: "plexichat:" is appropriate for most deployments
- **Custom**: Use a prefix that identifies your specific deployment (e.g., "plexichat-prod:")
- **Environment**: Consider environment-specific prefixes (e.g., "plexichat-staging:", "plexichat-dev:")
- **Multi-Instance**: Use unique prefixes for each Plexichat instance sharing Redis

**Best Practices**

- End prefix with a colon (":") for clarity
- Keep prefix short but descriptive
- Avoid special characters that may cause issues
- Document your prefix convention for your team

**Operational Notes**

- Use `redis-cli --scan --pattern "plexichat:*"` to list all Plexichat keys
- Use `redis-cli --scan --pattern "plexichat:*" | xargs redis-cli del` to clear all Plexichat data (use with caution)
- Consider using separate Redis databases (db 0-15) instead of prefixes for complete isolation

---

## TTL (Time-To-Live)

Configure automatic expiration of cached data.

### Configuration

```yaml
redis:
  ttl:
    session: 1800
    presence: 300
    cache: 60
```

### Deployment Considerations

**Why TTL Matters**

TTL prevents Redis from filling with stale data, ensuring memory usage remains bounded. Different data types have appropriate TTL values based on their volatility and importance.

**Session TTL (30 minutes default)**

- **Purpose**: User session data (tokens, authentication state)
- **Default**: 1800 seconds (30 minutes) balances security and user experience
- **High-Security**: Reduce to 900 seconds (15 minutes) for sensitive environments
- **Low-Security**: Increase to 3600 seconds (1 hour) for convenience
- **Refresh**: TTL is refreshed on activity, active users stay logged in

**Presence TTL (5 minutes default)**

- **Purpose**: User online status, typing indicators
- **Default**: 300 seconds (5 minutes) allows for brief network interruptions
- **High-Frequency**: Reduce to 120 seconds (2 minutes) for more accurate presence
- **Low-Frequency**: Increase to 600 seconds (10 minutes) to reduce Redis writes
- **Refresh**: Presence data is refreshed by client heartbeat

**Cache TTL (1 minute default)**

- **Purpose**: Generic query results, computed data
- **Default**: 60 seconds (1 minute) provides good performance without excessive staleness
- **High-Performance**: Reduce to 30 seconds for fresher data
- **Low-Performance**: Increase to 300 seconds (5 minutes) for higher cache hit rate
- **Trade-off**: Longer TTL improves cache hit rate but may serve stale data

**Operational Notes**

- Monitor Redis memory usage and key expiration rates
- Use `redis-cli info stats` to view key expiration statistics
- Adjust TTL values based on your application's data volatility requirements
- Consider implementing cache invalidation for critical data changes

---

## Cache Limits

Configure maximum cache size to prevent memory exhaustion.

### Configuration

```yaml
redis:
  cache_max_items: 1000
```

### Deployment Considerations

**Why Cache Limits Matter**

Without limits, caches can grow unbounded, consuming all available Redis memory and triggering eviction of important data. Setting limits ensures predictable memory usage.

**Cache Max Items**

- **Default**: 1000 items per cache category
- **Small Deployment**: 500-1000 items sufficient for low traffic
- **Medium Deployment**: 1000-5000 items for moderate traffic
- **Large Deployment**: 5000-20000 items for high traffic
- **Memory Impact**: Each item typically uses 1-10KB depending on data size

**Redis Memory Management**

Configure Redis memory settings in redis.conf:

```conf
maxmemory 256mb
maxmemory-policy allkeys-lru
```

**Recommended Policies**

- **allkeys-lru**: Evict least recently used keys (recommended for caching)
- **volatile-lru**: Evict least recently used keys with TTL set
- **allkeys-lfu**: Evict least frequently used keys (better for access patterns)
- **noeviction**: Return errors when memory limit reached (not recommended)

**Operational Notes**

- Monitor Redis `used_memory` and `used_memory_peak` metrics
- Set Redis `maxmemory` to 70-80% of available system RAM
- Use `redis-cli info memory` to view memory statistics
- Adjust cache_max_items based on actual memory usage patterns

---

## Scaling Considerations

### Single Redis Instance

**When to Use**

- Deployments with fewer than 10,000 concurrent users
- Simpler operational requirements
- Limited budget for Redis infrastructure

**Configuration Tips**

- Use Redis persistence (RDB + AOF) for data durability
- Enable replication for high availability (master + replica)
- Monitor memory usage and connection counts
- Plan for vertical scaling (more RAM, faster CPU)

### Redis Sentinel

**When to Use**

- Deployments requiring high availability
- Automatic failover requirements
- 10,000-100,000 concurrent users

**Configuration Tips**

- Deploy at least 3 Sentinel instances for quorum
- Configure automatic failover with appropriate down-after-milliseconds
- Monitor Sentinel health and failover events
- Update Plexichat config to use Sentinel (requires custom connection setup)

### Redis Cluster

**When to Use**

- Deployments with more than 100,000 concurrent users
- Horizontal scaling requirements
- Very large datasets (>100GB)

**Configuration Tips**

- Deploy at least 3 master nodes with replicas
- Use consistent hashing for data distribution
- Monitor cluster health and slot distribution
- Note: Redis Cluster does not support multiple databases (db 0-15)
- Requires custom connection setup in Plexichat

### Managed Redis Services

**Options**

- AWS ElastiCache for Redis
- Google Cloud Memorystore
- Azure Cache for Redis
- DigitalOcean Managed Databases for Redis

**Configuration Tips**

- Use provided connection endpoints and ports
- Enable SSL/TLS for all connections
- Configure appropriate node size based on expected load
- Use service-provided monitoring and alerting
- Leverage automatic failover and backup features

---

## Monitoring and Maintenance

### Key Metrics to Monitor

- **connected_clients**: Number of active connections
- **used_memory**: Current memory usage
- **used_memory_peak**: Peak memory usage
- **keyspace_hits**: Cache hit rate
- **keyspace_misses**: Cache miss rate
- **expired_keys**: Number of keys expired
- **evicted_keys**: Number of keys evicted due to memory limits

### Health Checks

```bash
# Check Redis connectivity
redis-cli -h <host> -p <port> -a <password> ping

# Check memory usage
redis-cli -h <host> -p <port> -a <password> info memory

# Check connection stats
redis-cli -h <host> -p <port> -a <password> info stats
```

### Maintenance Tasks

- **Regular Backups**: Enable RDB snapshots for point-in-time recovery
- **Memory Cleanup**: Monitor for memory leaks or unbounded key growth
- **Log Rotation**: Configure Redis log rotation to prevent disk space issues
- **Version Upgrades**: Plan for Redis version upgrades with testing

---

## Related Documentation

- [Default Configuration Reference](default-config.md) - Complete configuration reference
- [Database Configuration](config-database.md) - Database that Redis caches
- [Authentication Configuration](config-authentication.md) - Session storage in Redis
- [Deployment Guide](deployment.md) - Production deployment with Redis
