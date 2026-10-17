# Search configuration

This guide describes how to configure search for Plexichat. Search settings control how the server indexes and queries messages, servers, and users. All defaults in this guide come from `src/config_defaults.py:431`.

## Before you begin

You configure search under the `search` key in `config.yaml`.

```yaml
search:
  enabled: true
  backend: "sqlite_fts5"
  result_limit: 100
  batch_size: 100
  write_time_indexing: true
```

## Configure core search

```yaml
search:
  enabled: true
  backend: "sqlite_fts5"
  result_limit: 100
  batch_size: 100
  write_time_indexing: true
```

| Field | Default | Description |
|---|---|---|
| `enabled` | `true` | When `true`, the server indexes messages as they are created. When `false`, search endpoints return empty results. |
| `backend` | `sqlite_fts5` | Search backend. The default uses SQLite FTS5 virtual tables. |
| `result_limit` | `100` | Maximum results per query. Higher values increase memory use and response time. |
| `batch_size` | `100` | Number of documents indexed per batch during initial or background reindexing. |
| `write_time_indexing` | `true` | When `true`, the server indexes new messages immediately. |

Do not use the stale value `sqlite` for `backend`. The correct backend name is `sqlite_fts5` as defined in `src/config_defaults.py:433`. It provides better ranking and performance than plain SQLite.

### Enable search

Keep search enabled in most deployments. Disable it only on very small servers where the indexing overhead is unacceptable.

### Backend

`sqlite_fts5` is built in and requires no extra infrastructure. It handles most deployments up to about one million messages. The search module has a pluggable architecture for future backends such as Elasticsearch or Meilisearch.

### Result limit

The default `100` is appropriate because users rarely go beyond the first page. Increase it to `200`–`500` for archives where users need deep search, or decrease it to `50` if queries are slow on your hardware.

### Batch size

The default `100` balances indexing speed and memory use. Increase it to `500`–`1000` on high-memory servers to speed up initial indexing, or decrease it to `25`–`50` on low-memory servers.

### Write-time indexing

Keep `write_time_indexing` enabled so that new messages appear in search immediately. Disable it only on high-write deployments that exceed about 100 messages per second, and rely on periodic batch indexing instead.

## Configure server discovery

Discovery controls the public server directory.

```yaml
search:
  discovery:
    enabled: true
    min_members_for_listing: 10
    max_tags: 10
    bump_cooldown_hours: 4
```

| Field | Default | Description |
|---|---|---|
| `discovery.enabled` | `true` | When `true`, servers can appear in the discovery directory. When `false`, no servers appear regardless of individual server settings. |
| `discovery.min_members_for_listing` | `10` | Minimum members required before a server appears in discovery. |
| `discovery.max_tags` | `10` | Maximum tags a server owner can assign for categorization. |
| `discovery.bump_cooldown_hours` | `4` | Hours a server owner must wait before they can bump the server to a more prominent position. |

Do not use the stale key `discovery.min_members`. The correct key is `discovery.min_members_for_listing` as defined in `src/config_defaults.py:439`.

### Discovery enabled

Keep discovery enabled for public communities so users can find servers. Disable it for single-organization deployments where a public directory is unnecessary.

### Minimum members for listing

The default `10` prevents spam servers from flooding the directory. Reduce it to `5` to help new communities gain visibility, or increase it to `25`–`50` for large platforms.

### Maximum tags

The default `10` provides good categorization without tag spam. Reduce it to `5` for a simpler scheme or increase it to `15`–`20` for large directories that need fine-grained filtering.

### Bump cooldown

The default `4` hours prevents directory spam while letting active owners maintain visibility. Increase it to `8`–`12` hours for directories with many competing servers, or reduce it to `1`–`2` hours for small directories.

## Complete example

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

## Related documentation

*   [Default configuration reference](../../default-config.md) — Complete configuration reference.
*   [Database configuration](config-database.md) — SQLite and PostgreSQL setup for search indexes.
*   [Performance tuning](../../performance.md) — Optimizing search for large datasets.
