# API and server configuration

This guide describes how to configure the HTTP server and the REST API.

## Configuration keys

Use these keys in your `config.yaml`:

* `server` - Bind address, port, and auto-reload
* `api` - API metadata, CORS, trusted proxies, and documentation paths
* `tls` - TLS certificates for direct TLS termination

## Server settings

Configure the bind address and port under `server`. Do not use `api.host`
or `api.port`; the server reads `server.host` and `server.port`.

```yaml
server:
  host: 127.0.0.1  # Bind address. Use 0.0.0.0 to accept external connections.
  port: 8000       # HTTP port.
  reload: false    # Auto-reload on code changes. Enable only for development.
```

The `server` block has no `workers` key. To cap the thread pool for
`run_in_threadpool` work, set `api.max_worker_threads` (default `16`).

**Development:**

* Use `host: 127.0.0.1` and `reload: true` for local development.
* Keep `api.debug: true` only for development.

**Production:**

* Use `host: 0.0.0.0`.
* Set `reload: false` and `api.debug: false`.
* Run behind a reverse proxy such as nginx or Traefik. Let the proxy
  handle TLS, and keep Plexichat on HTTP internally.

## API settings

### API prefix and metadata

```yaml
api:
  title: Plexichat API
  description: REST API for the Plexichat messaging platform
  version: a.1.0-166
  api_prefix: /api/v1   # Route prefix for all REST endpoints. Do not use `prefix`.
  debug: false
  max_worker_threads: 16
```

`api_prefix` defaults to `/api/v1`. Change it only if you have a specific
routing requirement.

### Documentation paths

```yaml
api:
  docs_url: /docs          # Swagger UI. Set to null to disable.
  redoc_url: /redoc        # ReDoc. Set to null to disable.
  openapi_url: /openapi.json  # OpenAPI schema. Set to null to disable.
```

For production hardening, set `docs_url`, `redoc_url`, and `openapi_url` to
`null`, or protect them with authentication.

## CORS configuration

Plexichat uses flat keys under `api`, not a nested `api.cors` object.

```yaml
api:
  cors_origins:
    - https://app.plexichat.com
    - https://chat.example.com
  cors_allow_credentials: true
  cors_allow_methods:
    - GET
    - POST
    - PUT
    - PATCH
    - DELETE
    - OPTIONS
  cors_allow_headers:
    - Authorization
    - Content-Type
    - X-Requested-With
    - Accept
    - Origin
    - X-API-Access-Token
```

Default `cors_origins` includes `http://localhost:5000`,
`http://localhost:8000`, `https://plexichat.com`, and related origins.
Default `cors_allow_headers` also includes `X-Plexichat-Request` and
`X-RateLimit-Bypass`.

For production:

* List allowed origins explicitly.
* Never use `*` with `cors_allow_credentials: true`.
* Include only the origins that serve your web client.

For development, you can use `cors_origins: ["*"]` only when
`cors_allow_credentials` is `false`.

## Trusted proxies

When you run behind a reverse proxy, configure trusted proxies so the server
preserves the real client IP.

```yaml
api:
  trusted_proxies:
    - 10.0.0.0/8
    - 172.16.0.0/12
    - 127.0.0.1
  trust_x_forwarded_for: false
```

If you do not configure trusted proxies:

* Client IPs appear as the proxy IP.
* Rate limiting applies to the proxy as a single client.
* HTTPS detection can fail.

For Docker, include the Docker bridge ranges, for example `172.16.0.0/12`
and `192.168.0.0/16`.

## Debug mode

```yaml
api:
  debug: false
server:
  reload: false
```

Never enable `api.debug` or `server.reload` in production. Debug mode
exposes stack traces, enables the interactive debugger, and reduces
performance.

Use this only for local development:

```yaml
api:
  debug: true
server:
  reload: true
```

## TLS

TLS is configured under the top-level `tls` key, not under `api.ssl`.

```yaml
tls:
  enabled: false
  auto_generate_self_signed: false
  cert_path: /root/.plexichat/certs/server.crt
  key_path: /root/.plexichat/certs/server.key
  hostname: localhost
  cert_days: 365
```

Field reference:

* `enabled` - Whether to terminate TLS directly in Plexichat.
* `auto_generate_self_signed` - Generate a self-signed certificate if none
  exists.
* `cert_path`, `key_path` - Paths to the certificate and key files.
* `hostname` - Hostname to use for auto-generated certificates.
* `cert_days` - Validity period for auto-generated certificates.

Recommended production setup:

1. Terminate TLS at nginx or Traefik.
2. Run Plexichat with `tls.enabled: false` on HTTP internally.
3. Let the proxy manage certificates, for example with Let's Encrypt.

See the getting started guide for nginx examples.

## Complete examples

**Development:**

```yaml
server:
  host: 127.0.0.1
  port: 8000
  reload: true
api:
  api_prefix: /api/v1
  debug: true
  cors_origins:
    - http://localhost:5000
    - http://localhost:8000
  cors_allow_credentials: true
  docs_url: /docs
  redoc_url: /redoc
  openapi_url: /openapi.json
tls:
  enabled: false
```

**Production with reverse proxy:**

```yaml
server:
  host: 0.0.0.0
  port: 8000
  reload: false
api:
  api_prefix: /api/v1
  debug: false
  cors_origins:
    - https://app.plexichat.com
  cors_allow_credentials: true
  trusted_proxies:
    - 10.0.0.0/8
    - 172.16.0.0/12
  trust_x_forwarded_for: true
  docs_url: null
  redoc_url: null
  openapi_url: null
tls:
  enabled: false
```

## Related documentation

* [Default configuration reference](../../default-config.md)
* [Rate limiting configuration](config-rate-limiting.md)
* [WebSocket configuration](config-websocket.md)
* [Getting started](../getting-started.md)
* [Security](../../security.md)
