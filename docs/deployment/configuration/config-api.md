# API & Server Configuration

Configuration for the HTTP API server, including CORS, trusted proxies, debug mode, TLS, and documentation paths.

## Configuration Location

Add these settings to your `config.yaml` under the `api:` key.

## Core API Settings

### Host and Port

```yaml
api:
  host: "0.0.0.0"          # Bind address (0.0.0.0 for all interfaces)
  port: 8000               # HTTP port
  workers: 4               # Number of worker processes (0 = auto)
```

**Development:**
- Use `host: "127.0.0.1"` to only accept local connections
- Use `port: 8000` for local development
- Set `workers: 1` for easier debugging

**Production:**
- Use `host: "0.0.0.0"` to accept external connections
- Set `workers: 4` or more based on CPU cores
- Use a reverse proxy (nginx, Traefik) for TLS termination

### API Prefix

```yaml
api:
  prefix: "/api/v1"        # API route prefix
```

All REST API endpoints will be prefixed with this path. The default `/api/v1` should not be changed unless you have specific routing requirements.

## CORS Configuration

Cross-Origin Resource Sharing (CORS) controls which web origins can access your API.

```yaml
api:
  cors:
    enabled: true
    allow_origins:
      - "https://app.plexichat.com"
      - "https://chat.example.com"
    allow_methods:
      - "GET"
      - "POST"
      - "PUT"
      - "DELETE"
      - "PATCH"
      - "OPTIONS"
    allow_headers:
      - "*"
    allow_credentials: true
    max_age: 86400          # Preflight cache duration (seconds)
```

**Development:**
```yaml
api:
  cors:
    enabled: true
    allow_origins: ["*"]   # Allow all origins (less secure)
```

**Production:**
- Explicitly list allowed origins
- Never use `*` with `allow_credentials: true`
- Include your web client URL(s)

## Trusted Proxies

When running behind a reverse proxy, configure trusted proxy settings:

```yaml
api:
  trusted_proxies:
    - "10.0.0.0/8"         # Internal network
    - "172.16.0.0/12"      # Docker network
    - "127.0.0.1"          # Localhost
  trust_x_forwarded_for: true
  trust_x_forwarded_proto: true
  trust_x_forwarded_host: true
```

**Why this matters:**
- Client IP addresses will show the proxy IP instead of real client IPs
- Rate limiting will apply to the proxy collectively
- HTTPS detection may fail

**Docker Deployment:**
```yaml
api:
  trusted_proxies:
    - "172.16.0.0/12"      # Docker default network range
    - "192.168.0.0/16"     # Common Docker bridge
```

## Debug Mode

```yaml
api:
  debug: false             # Enable debug mode
  reload: false            # Auto-reload on code changes
```

**Never enable in production:**
- Exposes stack traces in error responses
- Enables interactive debugger (potential RCE)
- Reduces performance
- May leak sensitive configuration

**Development only:**
```yaml
api:
  debug: true
  reload: true
```

## Documentation Paths

```yaml
api:
  docs_url: "/docs"        # Swagger UI path (null to disable)
  redoc_url: "/redoc"     # ReDoc path (null to disable)
  openapi_url: "/openapi.json"  # OpenAPI schema path
```

**Production hardening:**
```yaml
api:
  docs_url: null           # Disable Swagger UI
  redoc_url: null          # Disable ReDoc
  openapi_url: null        # Disable OpenAPI schema
```

## TLS/SSL

While the API server supports direct TLS, using a reverse proxy is recommended:

```yaml
api:
  ssl:
    enabled: false
    cert_file: "/path/to/cert.pem"
    key_file: "/path/to/key.pem"
```

**Recommended production setup:**
1. Use nginx or Traefik for TLS termination
2. Run Plexichat on HTTP internally
3. Let the proxy handle certificate management (Let's Encrypt)

See [Getting Started](../getting-started.md) for nginx configuration examples.

## Request Body Limits

```yaml
api:
  max_request_body_size: 10485760    # 10 MB (bytes)
  max_multipart_memory: 67108864     # 64 MB for file uploads
```

Adjust based on your media upload requirements. Larger files should use chunked upload or direct S3 upload.

## Timeouts

```yaml
api:
  timeout:
    keep_alive: 75        # Keep-alive timeout (seconds)
    request: 60           # Request timeout (seconds)
```

## Complete Example

**Development:**
```yaml
api:
  host: "127.0.0.1"
  port: 8000
  workers: 1
  prefix: "/api/v1"
  debug: true
  reload: true
  cors:
    enabled: true
    allow_origins: ["*"]
  docs_url: "/docs"
  redoc_url: "/redoc"
  openapi_url: "/openapi.json"
```

**Production (with reverse proxy):**
```yaml
api:
  host: "0.0.0.0"
  port: 8000
  workers: 4
  prefix: "/api/v1"
  debug: false
  reload: false
  cors:
    enabled: true
    allow_origins:
      - "https://app.plexichat.com"
    allow_credentials: true
  trusted_proxies:
    - "10.0.0.0/8"
    - "172.16.0.0/12"
  trust_x_forwarded_for: true
  docs_url: null
  redoc_url: null
  openapi_url: null
```

## Security Considerations

1. **Disable debug mode** in production
2. **Use specific CORS origins** instead of `*`
3. **Configure trusted proxies** to preserve client IPs
4. **Disable docs** in production or protect with authentication
5. **Run behind reverse proxy** for TLS, rate limiting, and additional security

## Related Documentation

- [Default Configuration Reference](../../default-config.md) — Complete configuration reference
- [Rate Limiting Configuration](config-rate-limiting.md) — API rate limiting
- [WebSocket Configuration](config-websocket.md) — Gateway settings
- [Getting Started](../getting-started.md) — Production deployment guide
- [Security](../../security.md) — Security best practices
