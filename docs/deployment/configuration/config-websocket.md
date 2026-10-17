# WebSocket configuration

This guide describes how to configure the WebSocket gateway for Plexichat in production. WebSocket settings affect real-time reliability, resource use, and security.

## Configuration location

Add WebSocket settings to your `config.yaml` file under the `websocket` key (`src/config_defaults.py:164`):

```yaml
websocket:
  # WebSocket settings
```

## Core settings

### Configuration

```yaml
websocket:
  heartbeat_interval_ms: 45000
  session_timeout_ms: 60000
  max_connections_per_user: 5
  rate_limit_per_minute: 120
  max_message_size: 65536
  max_decompressed_size: 262144
  compression_enabled: true
  allowed_origins: []
```

Defaults are defined in `src/config_defaults.py:164`. The gateway uses them in `src/api/websocket/__init__.py:141`.

### Deployment considerations

**Heartbeat interval (`heartbeat_interval_ms`)**

*   The value is in milliseconds, not seconds.
*   The server sends a heartbeat and the client must reply with an ACK within `session_timeout_ms`.
*   Default is `45000` ms (45 seconds).
*   For high concurrency (thousands of connections), increase to `60000`–`90000` to reduce overhead.
*   For low latency, decrease to `15000`–`30000` to detect dead connections faster.

**Session timeout (`session_timeout_ms`)**

*   How long the server waits for a heartbeat ACK before it closes the connection.
*   The value must be greater than `heartbeat_interval_ms`.
*   Default is `60000` ms (60 seconds, 1.5x the heartbeat).
*   For unstable mobile networks, increase to `90000`–`120000`.
*   For stable datacenter links, you can use `45000`.

**Maximum connections per user (`max_connections_per_user`)**

*   Limits simultaneous WebSocket connections for one user. When the limit is reached, the oldest connection closes.
*   Default is `5`, which covers multiple devices and browser tabs.
*   Increase to `10` for power users or decrease to `3` on resource-constrained hosts.

**Rate limit per minute (`rate_limit_per_minute`)**

*   Maximum messages that one WebSocket connection can send per minute.
*   Default is `120`.
*   Increase to `180`–`240` for bot-heavy deployments. Decrease to `60`–`80` to reduce spam risk.

## Message size limits

### Configuration

```yaml
websocket:
  max_message_size: 65536      # 64 KB. Defined in src/config_defaults.py:169
  max_decompressed_size: 262144  # 256 KB. Defined in src/config_defaults.py:170
```

### Deployment considerations

**Max message size (`max_message_size`)**

*   Maximum size of one WebSocket frame in bytes. Plexichat rejects larger frames and may close the connection.
*   Default is `65536` (64 KB). This is lower than an earlier 1 MB claim — the 64 KB cap protects against memory exhaustion and denial-of-service attacks.
*   Use `65536` for standard chat, including rich embeds.
*   Increase to `131072` (128 KB) only if you send very large embeds. Do not exceed `524288` (512 KB).

**Max decompressed size (`max_decompressed_size`)**

*   Maximum size after decompression when `compression_enabled` is `true`. This mitigates zip-bomb attacks.
*   Default is `262144` (256 KB, 4x `max_message_size`).
*   Do not increase this value without a specific need and sufficient server memory.

## Compression

### Configuration

```yaml
websocket:
  compression_enabled: true   # Default in src/config_defaults.py:171
```

### Deployment considerations

`compression_enabled` controls per-message deflate (`RFC 7692`).

*   Keep compression enabled for most deployments. Chat messages typically compress by 60–80%.
*   Disable it (`false`) when the host is CPU-bound rather than bandwidth-bound. Compression adds about 5–15% CPU overhead per connection.
*   Each compressed connection keeps a compression context (about 64–256 KB). For 10,000 connections, expect 0.6–2.5 GB of extra memory.
*   When compression is enabled, `max_decompressed_size` is your defense against decompression bombs. Keep it at `262144` or lower.

## Allowed origins

### Configuration

```yaml
websocket:
  allowed_origins: []   # Default is empty (allow all). Defined in src/config_defaults.py:172
```

### Deployment considerations

*   When the list is empty, Plexichat accepts connections from any origin. Use this only for development.
*   In production, list every origin that hosts your web client:

    ```yaml
    websocket:
      allowed_origins:
        - "https://app.plexichat.com"
        - "https://plexichat.com"
    ```

*   Wildcards are not supported. List each origin explicitly.
*   An empty list is compatible with `localhost` development but exposes you to cross-site WebSocket hijacking (CSWSH).

The gateway validates the `Origin` header in `src/api/websocket/gateway.py`.

## Cross-worker scaling with Valkey

When you run multiple gateway workers, enable Valkey (`valkey.enabled: true`). The gateway starts a dedicated pub/sub listener on `{key_prefix}events:cross_worker` (`src/api/websocket/__init__.py:342`). The listener uses exponential backoff and you can inspect its health at `src/api/websocket/__init__.py:100` with `cross_worker_listener_status()`.

Without Valkey, presence and events are limited to a single worker.

## Complete production example

```yaml
websocket:
  heartbeat_interval_ms: 45000
  session_timeout_ms: 60000
  max_connections_per_user: 5
  rate_limit_per_minute: 120
  max_message_size: 65536
  max_decompressed_size: 262144
  compression_enabled: true
  allowed_origins:
    - "https://app.plexichat.com"
    - "https://plexichat.com"
```

## Key name accuracy

The following keys use milliseconds and have smaller defaults than some older documentation claimed:

| Outdated key or value | Correct key and default | Source |
| --- | --- | --- |
| `heartbeat_interval_seconds` | `heartbeat_interval_ms: 45000` | `src/config_defaults.py:165` |
| `heartbeat_timeout_seconds` | `session_timeout_ms: 60000` | `src/config_defaults.py:166` |
| `max_message_size: 1048576` (1 MB) | `max_message_size: 65536` (64 KB) | `src/config_defaults.py:169` |
| `max_decompressed_size: 10485760` (10 MB) | `max_decompressed_size: 262144` (256 KB) | `src/config_defaults.py:170` |

## Related documentation

*   [Default configuration reference](../../default-config.md)
*   [API and server configuration](config-api.md)
*   [Rate limiting configuration](config-rate-limiting.md)
*   [Security best practices](../../security.md)
