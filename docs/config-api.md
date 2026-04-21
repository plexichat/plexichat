# API & Server Configuration

This guide covers API and server configuration for deploying Plexichat in production. These settings control how the API server behaves, handles cross-origin requests, and exposes documentation. Proper configuration is essential for security and interoperability.

## Configuration Location

API and server settings are split across the `server`, `api`, and `tls` keys in your configuration file:

```yaml
server:
  # Host, port, workers, reload

api:
  # Title, CORS, proxies, docs paths
```

## Server Settings

### Configuration

```yaml
server:
  host: "127.0.0.1"
  port: 8000
  workers: 1
  reload: false
```

### Deployment Considerations

**Host (127.0.0.1 default)**

- The bind address for the HTTP server. `127.0.0.1` listens only on localhost — appropriate when running behind a reverse proxy (nginx, Caddy, Traefik).
- **Behind Reverse Proxy**: Keep `127.0.0.1`. The proxy handles external connections.
- **Direct Internet Access**: Change to `0.0.0.0` to listen on all interfaces. Ensure a firewall is configured.
- **Docker**: Use `0.0.0.0` inside the container and map the port via Docker networking.

**Port (8000 default)**

- The TCP port the server listens on.
- **Standard Deployment**: 8000 is fine for most cases.
- **Multiple Instances**: Use different ports when running multiple Plexichat instances on the same host.

**Workers (1 default)**

- Number of worker processes. Plexichat uses Uvicorn with async I/O, so a single worker can handle many concurrent connections.
- **Standard Deployment**: 1 worker with async I/O handles hundreds of concurrent connections.
- **High-Traffic**: Increase to 2-4 workers for CPU-bound workloads (encryption, media processing). Each worker runs its own event loop.
- **Note**: When using multiple workers, ensure Redis is enabled for shared session state. See [Redis Configuration](config-redis.md).

**Reload (false default)**

- Enables auto-reload when source files change. **Never enable in production** — it restarts the server on every file change.
- **Development**: Set to `true` for a smoother development workflow.
- **Production**: Always `false`.

---

## API Settings

### Configuration

```yaml
api:
  title: "Plexichat API"
  description: "REST API for the Plexichat messaging platform"
  api_prefix: "/api/v1"
  debug: true
  cors_origins:
    - "http://localhost:5000"
    - "http://127.0.0.1:5000"
    - "http://localhost:8000"
    - "http://127.0.0.1:8000"
    - "https://plexichat.com"
    - "https://app.plexichat.com"
    - "https://api.plexichat.com"
    - "http://localhost:8443"
  cors_allow_credentials: true
  cors_allow_methods: ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
  cors_allow_headers: ["Authorization", "Content-Type", "X-Requested-With", "Accept", "Origin"]
  trusted_proxies: []
  trust_x_forwarded_for: false
  docs_url: "/docs"
  redoc_url: "/redoc"
  openapi_url: "/openapi.json"
```

### Deployment Considerations

**Debug Mode (true default)**

- **Development Default**: Debug mode is enabled by default for development convenience. It provides detailed error messages and stack traces.
- **Production**: **Must set to `false`**. Debug mode exposes internal implementation details in error responses, which is a security risk.
- Debug mode also affects error handling middleware behavior — in debug mode, unhandled exceptions return full tracebacks; in production mode, they return generic 500 errors.

**CORS Origins**

- The key is `cors_origins`, not `allow_origins`. The default is a specific list of localhost and production URLs, **not `["*"]`** as some previous documentation claimed.
- CORS (Cross-Origin Resource Sharing) controls which domains can make browser requests to your API.
- **Production**: Replace the default origins with your actual client domains:
  ```yaml
  api:
    cors_origins:
      - "https://your-app.example.com"
      - "https://plexichat.yourcompany.internal"
  ```
- **Wildcard**: Setting `["*"]` allows all origins. This is insecure for production but convenient for development.
- **Credentials**: When `cors_allow_credentials: true` (default), you cannot use `["*"]` — browsers reject credential-bearing requests with wildcard origins. You must explicitly list origins.

**Trusted Proxies**

- `trusted_proxies: []` — List of trusted reverse proxy IP addresses. Used for correctly identifying client IPs behind proxies.
- `trust_x_forwarded_for: false` — Whether to trust the `X-Forwarded-For` header for client IP identification.
- **Behind Reverse Proxy**: Configure both:
  ```yaml
  api:
    trusted_proxies:
      - "10.0.0.1"    # nginx/load balancer IP
      - "10.0.0.2"
    trust_x_forwarded_for: true
  ```
- **Direct Internet**: Keep defaults (empty proxies, trust disabled). Do not enable `trust_x_forwarded_for` without configuring `trusted_proxies` — clients could spoof their IP to bypass IP-based rate limiting.

**Impact on Rate Limiting**: If you use a reverse proxy but don't configure trusted proxies, all requests appear to come from the proxy IP. This means:
- Per-IP rate limits are applied to all users collectively (everyone hits the same limit)
- Per-IP rate limiting is effectively broken

See [Rate Limiting Configuration](config-rate-limiting.md) for details.

**Documentation Paths**

- `docs_url: "/docs"` — Swagger UI path
- `redoc_url: "/redoc"` — ReDoc path
- `openapi_url: "/openapi.json"` — OpenAPI schema path
- **Standard Deployment**: Default paths are fine.
- **Security**: Can set to `null` to disable individual documentation surfaces in production.

---

## TLS Settings

### Configuration

```yaml
tls:
  enabled: false
  auto_generate_self_signed: false
  cert_path: "~/.plexichat/certs/server.crt"
  key_path: "~/.plexichat/certs/server.key"
  cert_days: 365
```

### Deployment Considerations

**TLS Enabled (false default)**

- Plexichat can serve TLS directly, but most deployments use a reverse proxy for TLS termination instead.
- **With Reverse Proxy**: Keep `tls.enabled: false`. Configure TLS on your proxy (nginx, Caddy, Traefik).
- **Without Reverse Proxy**: Enable TLS and provide certificate paths:
  ```yaml
  tls:
    enabled: true
    cert_path: "/etc/ssl/certs/plexichat.crt"
    key_path: "/etc/ssl/private/plexichat.key"
  ```
- **Self-Signed**: `auto_generate_self_signed: true` creates a self-signed certificate for testing. **Never use in production** — browsers will show security warnings.

**Certificate Paths**

- `cert_path` and `key_path` support `~` expansion for home directory.
- Ensure the application user has read access to certificate files.
- For Let's Encrypt certificates, use the fullchain certificate file.

---

## Complete Production Example

```yaml
server:
  host: "127.0.0.1"
  port: 8000
  workers: 2
  reload: false

api:
  title: "Plexichat API"
  api_prefix: "/api/v1"
  debug: false
  cors_origins:
    - "https://app.yourcompany.com"
  cors_allow_credentials: true
  trusted_proxies:
    - "10.0.0.1"
  trust_x_forwarded_for: true
  docs_url: "/docs"
  redoc_url: null

tls:
  enabled: false
```

---

## Key Name Accuracy

| Common Assumption | Actual Key | Notes |
|---|---|---|
| `allow_origins: ["*"]` | `cors_origins: [...]` | Key is `cors_origins`, default is specific list |
| `proxy_headers` | `trusted_proxies` + `trust_x_forwarded_for` | Split into two keys |
| `debug: false` (default) | `debug: true` | Default is true for development; must set false for production |

---

## Related Documentation

- [Default Configuration Reference](default-config.md) — Complete configuration reference
- [Rate Limiting Configuration](config-rate-limiting.md) — Rate limits depend on correct proxy configuration
- [Security Best Practices](security.md) — TLS, CORS, and proxy security
- [Deployment Guide](deployment.md) — Full deployment walkthrough
