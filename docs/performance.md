# Performance Optimization Guide

This document describes the performance optimizations implemented in PlexiChat and how to configure them for optimal performance.

## Target Performance Goals

- **Message send latency**: < 50ms end-to-end
- **API response time**: < 100ms for most endpoints
- **WebSocket event delivery**: < 20ms after message creation
- **Cache hit rate**: > 80% for stable data

## Client-Side Caching

### In-Memory Cache (PlexiAPI)

The client maintains an in-memory cache with configurable TTLs for different data types:

```javascript
// Cache TTL configuration (milliseconds)
'/users/@me': 300000,           // 5 minutes - user data rarely changes
'/users/@me/features': 600000,  // 10 minutes - tier/badges very stable
'/users/@me/settings': 300000,  // 5 minutes - settings rarely change
'/servers': 120000,             // 2 minutes - server list
'/servers/': 180000,            // 3 minutes - individual server data
'/relationships/@me': 300000,   // 5 minutes - friends rarely change
'/users/': 300000,              // 5 minutes - user profiles
'/channels/': 120000,           // 2 minutes - channel data
'/health': 30000,               // 30 seconds - health checks
'/version': 600000,             // 10 minutes - version rarely changes
'/status': 10000,               // 10 seconds - status can change
'default': 60000                // 1 minute default
```

### Cache Features

1. **Request Deduplication**: Concurrent GET requests to the same endpoint are deduplicated
2. **Automatic Invalidation**: Related caches are invalidated on mutations
3. **Batch User Lookup**: `getUsersBatch()` fetches multiple users with limited concurrency
4. **Cache Preloading**: `preloadCommonData()` warms cache on app startup
5. **Cache Statistics**: `getCacheStats()` for debugging and monitoring

### Message Caching

Messages are cached in IndexedDB with a 2-minute freshness window:
- Fresh cache: Skip API call entirely
- Stale cache: Show cached data immediately, fetch updates in background
- Cache miss: Show loading state, fetch from API

## Database Optimizations

### SQLite Configuration
When using SQLite, the following PRAGMA settings are automatically applied:
- `journal_mode=WAL` - Write-Ahead Logging for better concurrent read/write
- `synchronous=NORMAL` - Balanced durability and performance
- `cache_size=-64000` - 64MB cache
- `mmap_size=268435456` - 256MB memory-mapped I/O
- `temp_store=MEMORY` - In-memory temp tables
- `foreign_keys=ON` - Referential integrity

### PostgreSQL Configuration
For PostgreSQL, connection pooling is enabled by default:
```yaml
database:
  connection_pool:
    min_connections: 5
    max_connections: 20
```

## Redis Caching

Enable Redis for significant performance improvements:

```yaml
redis:
  enabled: true
  host: 127.0.0.1
  port: 6379
  ttl:
    session: 1800    # 30 minutes
    presence: 300    # 5 minutes
    cache: 60        # 1 minute
```

### What Gets Cached in Redis
- **Token verification** (30s TTL) - Avoids DB lookup on every request
- **Server data** (5 min TTL) - Server info, member counts
- **User data** - User profiles and settings
- **Presence data** - Online status, activities
- **Rate limit counters** - Per-user and per-IP limits

### Cache Logging

Cache hits and misses are logged at INFO level:
```
CACHE HIT: cache:module.function:arg1:arg2 (total hits: 123)
CACHE MISS: cache:module.function:arg1:arg2 (total misses: 45)
```

## API Optimizations

### Batch Operations
The following batch operations are implemented to avoid N+1 queries:

1. **Batch User Lookup** - `auth.get_users_bulk(user_ids)`
2. **Batch Reactions Fetch** - `reactions.get_reactions_batch(user_id, message_ids)`
3. **Batch Attachments Fetch** - `messaging._get_attachments_batch(message_ids)`
4. **Batch Presence Fetch** - `presence.get_presences(user_ids)`

### Token Username Optimization
The username is extracted from the JWT token and reused throughout the request, avoiding redundant user lookups.

### Message Send Optimization
Message creation is optimized to:
1. Use cached channel info when available
2. Skip redundant permission checks
3. Broadcast via WebSocket asynchronously (doesn't block response)
4. Return response immediately while broadcast happens in background

## In-Memory Caching

Each module maintains in-memory caches with configurable TTL:
- `_member_cache` - Server membership checks
- `_permission_cache` - Permission calculations
- `_participant_cache` - Conversation participant checks
- `_user_settings_cache` - User message settings

Default TTL: 30-60 seconds

### Memory Safety & Resource Capping

To prevent Out-Of-Memory (OOM) scenarios, PlexiChat implements strict resource capping:

1.  **Capped In-Memory Caches**: All module-level caches use a LRU-style eviction policy. The maximum number of items is configurable via `redis.cache_max_items` (default: 1000).
2.  **Streaming Media Proxy**: The external URL proxy streams content directly to storage with a small memory buffer (default: 64KB), preventing large file downloads from consuming excessive RAM.
3.  **Media Size Limits**: Enforced at the proxy level (`proxy_max_size`) and upload level to ensure process stability.

```yaml
redis:
  cache_max_items: 1000  # Max items per in-process cache

media:
  proxy_buffer_size: 65536  # 64KB streaming buffer
```

## WebSocket Optimizations

### Connection Pooling
- Max 5 connections per user
- Session resume support (60s timeout)
- Event replay buffer (100 events)

### Event Dispatch
- Events are dispatched only to connected users
- Intent filtering reduces unnecessary broadcasts
- Rate limiting prevents event flooding (120/min)
- Async dispatch doesn't block API responses

### Real-time Message Delivery
When a message is sent:
1. API returns response immediately (~20-30ms)
2. Background task broadcasts MESSAGE_CREATE to all channel members
3. Connected clients receive event via WebSocket (~10-20ms additional)

## Configuration Recommendations

### Development
```yaml
redis:
  enabled: false
database:
  type: sqlite
logging:
  level: DEBUG
```

### Production
```yaml
redis:
  enabled: true
  connection_pool:
    max_connections: 50
database:
  type: postgres
  connection_pool:
    min_connections: 5
    max_connections: 20
logging:
  level: INFO
rate_limiting:
  enabled: true
```

## Monitoring

### Cache Statistics
Access cache statistics via the health endpoint:
```
GET /api/v1/health
```

### Client Cache Stats
In browser console:
```javascript
PlexiAPI.getCacheStats()
// Returns: { totalEntries, expiredEntries, activeEntries, pendingRequests }
```

### Database Metrics
Monitor connection pool usage and query performance through PostgreSQL's `pg_stat_activity` or SQLite's `PRAGMA database_list`.

## Known Bottlenecks

1. **Large Server Member Lists** - For servers with 10k+ members, consider pagination
2. **Message Search** - Full-text search on large channels can be slow without proper indexing
3. **Attachment Processing** - Large file uploads are processed synchronously

## Future Improvements

- [ ] Elasticsearch integration for message search
- [ ] Read replicas for PostgreSQL
- [ ] CDN integration for media delivery
- [ ] Background job queue for heavy operations
- [ ] HTTP/2 push for predictive data loading
- [ ] Service worker for offline support
