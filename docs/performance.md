# Performance Optimization Guide

This document describes the performance optimizations implemented in PlexiChat and how to configure them for optimal performance.

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
    min_connections: 2
    max_connections: 20
```

## Redis Caching

Enable Redis for significant performance improvements:

```yaml
redis:
  enabled: true
  host: localhost
  port: 6379
  ttl:
    session: 1800    # 30 minutes
    presence: 300    # 5 minutes
    cache: 60        # 1 minute
```

### What Gets Cached
- **Token verification** (30s TTL) - Avoids DB lookup on every request
- **Server data** (5 min TTL) - Server info, member counts
- **User data** - User profiles and settings
- **Presence data** - Online status, activities
- **Rate limit counters** - Per-user and per-IP limits

## API Optimizations

### Batch Operations
The following batch operations are implemented to avoid N+1 queries:

1. **Batch User Lookup** - `auth.get_users_bulk(user_ids)`
2. **Batch Reactions Fetch** - `reactions.get_reactions_batch(user_id, message_ids)`
3. **Batch Attachments Fetch** - `messaging._get_attachments_batch(message_ids)`
4. **Batch Presence Fetch** - `presence.get_presences(user_ids)`

### Token Username Optimization
The username is extracted from the JWT token and reused throughout the request, avoiding redundant user lookups.

## In-Memory Caching

Each module maintains in-memory caches with configurable TTL:
- `_member_cache` - Server membership checks
- `_permission_cache` - Permission calculations
- `_participant_cache` - Conversation participant checks
- `_user_settings_cache` - User message settings

Default TTL: 30-60 seconds

## WebSocket Optimizations

### Connection Pooling
- Max 5 connections per user
- Session resume support (60s timeout)
- Event replay buffer (100 events)

### Event Dispatch
- Events are dispatched only to connected users
- Intent filtering reduces unnecessary broadcasts
- Rate limiting prevents event flooding (120/min)

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
    min_connections: 2
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
