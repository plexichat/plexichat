# WebSocket Configuration

This guide covers WebSocket gateway configuration for deploying Plexichat in production. WebSocket settings directly impact real-time communication reliability, resource consumption, and security. Carefully review each section and adjust values according to your expected concurrent connection count and network conditions.

## Configuration Location

All WebSocket settings are nested under the `websocket` key in your configuration file:

```yaml
websocket:
  # All WebSocket settings go here
```

## Core Settings

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

### Deployment Considerations

**Heartbeat Interval (45 seconds default)**

- The key is `heartbeat_interval_ms`, not `heartbeat_interval_seconds`. The value is in **milliseconds**.
- Controls how frequently the server sends heartbeat packets to connected clients. Clients must respond with a heartbeat ACK within the session timeout window.
- **Standard Deployment**: 45 seconds provides reliable connection monitoring without excessive overhead.
- **High-Concurrency Deployment**: Increase to 60,000-90,000 ms (60-90 seconds) to reduce per-connection overhead when serving thousands of concurrent connections.
- **Low-Latency Deployment**: Decrease to 15,000-30,000 ms (15-30 seconds) to detect dead connections faster, at the cost of more network traffic.

**Session Timeout (60 seconds default)**

- How long the server waits for a heartbeat ACK before terminating the connection. Must be greater than `heartbeat_interval_ms`.
- If a client fails to respond within this window, the connection is dropped and the session is invalidated.
- **Standard Deployment**: 60 seconds (1.5x heartbeat) provides adequate tolerance for network jitter.
- **Unstable Networks**: Increase to 90,000-120,000 ms for users on mobile networks or high-latency connections.
- **Stable Networks**: Can decrease to 45,000 ms for datacenter-to-datacenter communication.

**Maximum Connections Per User (5 default)**

- Limits how many simultaneous WebSocket connections a single user can maintain. When exceeded, the oldest connection is dropped.
- **Standard Deployment**: 5 connections accommodates a user with multiple devices (phone, tablet, desktop, browser tab).
- **Power Users**: Consider increasing to 10 for communities where users frequently use multiple clients.
- **Resource-Constrained**: Decrease to 3 to limit per-user resource consumption on small servers.

**Rate Limit Per Minute (120 default)**

- Maximum number of messages a single WebSocket connection can send per minute. Applies to all message types (events, heartbeats, etc.).
- **Standard Deployment**: 120 messages/minute is generous for normal usage.
- **Bot-Heavy Communities**: Bots may need higher limits. Consider increasing to 180-240 for developer-friendly deployments.
- **Spam Prevention**: Decrease to 60-80 to limit potential for spam through compromised accounts.

---

## Message Size Limits

### Configuration

```yaml
websocket:
  max_message_size: 65536
  max_decompressed_size: 262144
```

### Deployment Considerations

**Max Message Size (65536 bytes / 64KB default)**

- Maximum size of a single WebSocket frame in bytes. Messages exceeding this are rejected and the connection may be terminated.
- This is significantly lower than the 1MB some documentation previously claimed. The 64KB limit protects against memory exhaustion and denial-of-service attacks.
- **Standard Deployment**: 64KB is sufficient for all normal messages, including rich embeds and attachments metadata.
- **Large Embeds**: If your clients send very large embed payloads, increase to 131072 (128KB). Do not exceed 524288 (512KB).
- **Minimal**: Decrease to 32768 (32KB) for maximum security on public-facing servers.

**Max Decompressed Size (262144 bytes / 256KB default)**

- Maximum size of a decompressed WebSocket message after decompression. Messages that decompress beyond this limit are rejected.
- This prevents zip-bomb attacks where a small compressed payload expands to an enormous size.
- **Standard Deployment**: 256KB is 4x the max message size, providing adequate room for compression.
- **Do not increase** unless you have a specific use case that requires it and you have sufficient server memory.

---

## Compression

### Configuration

```yaml
websocket:
  compression_enabled: true
```

### Deployment Considerations

**Why Compression Matters**

WebSocket compression (per-message deflate, RFC 7692) significantly reduces bandwidth usage for text-heavy real-time communication. However, compression has CPU and memory costs.

**Production Recommendations**

- **Enable Compression**: Keep enabled for most deployments. Bandwidth savings of 60-80% are typical for chat messages.
- **CPU-Bound Servers**: Disable (`compression_enabled: false`) if your server is CPU-constrained rather than bandwidth-constrained. Compression adds ~5-15% CPU overhead per connection.
- **High-Concurrency**: Monitor memory usage. Each compressed connection maintains a compression context (typically 64-256KB). With 10,000 connections, this adds 0.6-2.5GB of memory.

**Security Considerations**

- The `max_decompressed_size` setting is critical when compression is enabled. Without it, a malicious client could send a tiny compressed payload that decompresses to gigabytes.
- Keep `max_decompressed_size` at 256KB or lower. This is your defense against compression-based denial-of-service.

---

## Allowed Origins

### Configuration

```yaml
websocket:
  allowed_origins: []
```

### Deployment Considerations

**Origin Validation**

- When `allowed_origins` is empty (default), all origins are accepted. This is appropriate for development but risky for production.
- **Production Deployment**: Explicitly list your allowed client origins:
  ```yaml
  websocket:
    allowed_origins:
      - "https://app.plexichat.com"
      - "https://plexichat.com"
  ```
- **Multi-Tenant**: Include all domains that host your web client.
- **Development**: Keep empty to allow `localhost` connections.

**Security Trade-offs**

- **Empty Origins**: Maximum compatibility, no origin validation, vulnerable to cross-site WebSocket hijacking (CSWSH).
- **Explicit Origins**: Prevents unauthorized websites from making WebSocket connections to your server. Recommended for production.
- **Wildcard**: Not supported - explicitly list each allowed origin.

---

## Complete Production Example

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

---

## Key Name Accuracy

- ``heartbeat_interval_seconds`` (`heartbeat_interval_ms`): Uses milliseconds, not seconds
- ``heartbeat_timeout_seconds`` (`session_timeout_ms`): Uses milliseconds, not seconds
- ``max_message_size: 1048576`` (`max_message_size: 65536`): Default is 64KB, not 1MB
- ``max_decompressed_size: 10485760`` (`max_decompressed_size: 262144`): Default is 256KB, not 10MB

---

## Related Documentation

- [Default Configuration Reference](../../default-config.md) - Complete configuration reference
- [API & Server Configuration](deployment/configuration/config-api.md) - CORS, proxies, debug mode
- [Rate Limiting Configuration](deployment/configuration/config-rate-limiting.md) - Global, user, IP limits
- [Security Best Practices](../../security.md) - WebSocket security considerations
