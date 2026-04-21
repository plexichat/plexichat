# Search Configuration

This guide covers search configuration for deploying Plexichat in production. Search settings control how messages, servers, and users are indexed and queried. Proper configuration ensures fast, relevant search results while managing resource consumption.

## Configuration Location

All search settings are nested under the `search` key in your configuration file:

```yaml
search:
  # All search settings go here
```

## Core Settings

### Configuration

```yaml
search:
  enabled: true
  backend: "sqlite_fts5"
  result_limit: 100
  batch_size: 100
  write_time_indexing: true
```

### Deployment Considerations

**Search Enabled (true default)**

- When enabled, messages and content are indexed as they are created, making them immediately searchable.
- **Standard Deployment**: Keep enabled. Search is a core feature users expect.
- **Resource-Constrained**: Disable (`enabled: false`) on very small servers where indexing overhead is unacceptable. All search endpoints will return empty results.

**Backend ("sqlite_fts5" default)**

- The key is `backend`, and the default value is `"sqlite_fts5"`, not `"sqlite"`. The FTS5 variant uses SQLite's full-text search extension for significantly better search performance and relevance ranking.
- **sqlite_fts5**: Built-in, zero-dependency, uses SQLite's FTS5 virtual tables. Appropriate for most deployments up to ~1M messages. No additional infrastructure required.
- **Custom Backends**: The search module supports a pluggable backend architecture. Future backends may include Elasticsearch or Meilisearch for large-scale deployments.

**Result Limit (100 default)**

- Maximum number of search results returned per query. Higher values increase memory usage and response time for broad queries.
- **Standard Deployment**: 100 results is appropriate for most use cases. Users rarely paginate beyond the first page of results.
- **Large Archives**: Can increase to 200-500 for deployments with extensive message history where users need deep search.
- **Performance**: Decrease to 50 if search queries are slow on your hardware.

**Batch Size (100 default)**

- Number of documents indexed per batch during initial indexing and background reindexing operations. Larger batches index faster but consume more memory.
- **Standard Deployment**: 100 is a safe balance between indexing speed and memory usage.
- **High-Memory Servers**: Increase to 500-1000 for faster initial indexing when deploying with large existing message databases.
- **Low-Memory Servers**: Decrease to 25-50 to reduce peak memory usage during indexing.

**Write-Time Indexing (true default)**

- When enabled, new messages are indexed immediately upon creation. This ensures search results are always up-to-date.
- **Standard Deployment**: Keep enabled. Users expect newly sent messages to appear in search immediately.
- **High-Write Deployments**: If your server handles very high message throughput (>100 msg/sec), consider disabling write-time indexing and relying on periodic batch indexing to reduce write amplification on the database.

---

## Server Discovery

### Configuration

```yaml
search:
  discovery:
    enabled: true
    min_members_for_listing: 10
    max_tags: 10
    bump_cooldown_hours: 4
```

### Deployment Considerations

**Discovery Enabled (true default)**

- Controls whether servers can be listed in the public server discovery directory. When disabled, no servers appear in discovery regardless of their individual settings.
- **Public Communities**: Keep enabled to allow users to find and join public servers.
- **Private Deployments**: Disable if Plexichat is deployed for a single organization where server discovery is not needed.

**Minimum Members for Listing (10 default)**

- Servers must have at least this many members before they appear in discovery. This prevents spam servers from flooding the directory.
- **Standard Deployment**: 10 members ensures only established communities are visible.
- **New Communities**: Consider reducing to 5 to help new servers gain visibility.
- **Large Platforms**: Increase to 25-50 for high-traffic deployments to ensure only substantial communities appear.

**Maximum Tags (10 default)**

- Maximum number of tags a server owner can assign to their server for discovery categorization. Tags help users find servers matching their interests.
- **Standard Deployment**: 10 tags provides good categorization without tag spam.
- **Minimal**: Reduce to 5 for simpler categorization schemes.
- **Detailed**: Increase to 15-20 for large directories where fine-grained tagging improves discoverability.

**Bump Cooldown (4 hours default)**

- How long a server owner must wait before they can "bump" their server in the discovery listing (moving it to a more prominent position).
- **Standard Deployment**: 4 hours prevents discovery spam while allowing active owners to maintain visibility.
- **High-Traffic**: Increase to 8-12 hours when the directory has many servers competing for attention.
- **Small Directories**: Can reduce to 1-2 hours when there are few servers and bump frequency doesn't degrade the experience.

---

## Complete Production Example

```yaml
search:
  enabled: true
  backend: "sqlite_fts5"
  result_limit: 100
  batch_size: 100
  write_time_indexing: true
  discovery:
    enabled: true
    min_members_for_listing: 10
    max_tags: 10
    bump_cooldown_hours: 4
```

---

## Key Name Accuracy

| Common Assumption | Actual Key | Notes |
|---|---|---|
| `backend: "sqlite"` | `backend: "sqlite_fts5"` | Uses the FTS5 variant for full-text search |
| `discovery.min_members` | `discovery.min_members_for_listing` | Full key name includes "for_listing" |

---

## Related Documentation

- [Default Configuration Reference](default-config.md) — Complete configuration reference
- [Database Configuration](config-database.md) — SQLite/PostgreSQL setup for search indexes
- [Performance Tuning](performance.md) — Optimizing search for large datasets
