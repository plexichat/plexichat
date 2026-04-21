# Getting Started

This guide walks you through connecting to a Plexichat server, authenticating, and making your first API requests. Whether you're building a client, a bot, or an integration, start here.

## 1. Identify Your Server Endpoints

Before making any requests, you need three URLs:

| Endpoint | Default | Purpose |
|----------|---------|---------|
| REST API | `http://localhost:8000/api/v1` | All REST operations |
| WebSocket Gateway | `ws://localhost:8000/gateway` | Real-time events |
| Documentation | `http://localhost:8000/docs/api` | This portal |

Your server administrator may provide different URLs. The documentation portal is served at the `docs.path` configuration value (default: `/docs/api`).

## 2. Check Server Compatibility

Before relying on feature-specific behavior, verify the server version and capabilities:

```bash
# Get server version
curl {{BASE_URL}}/version

# Negotiate compatibility
curl -X POST {{BASE_URL}}/version/negotiate \
  -H "Content-Type: application/json" \
  -d '{"client_version":"{{VERSION}}"}'

# Discover capabilities (avatar limits, access token requirement, etc.)
curl {{BASE_URL}}/capabilities
```

The `/capabilities` endpoint returns public constants without authentication. Use it to adapt your client to the server's configuration (e.g., maximum avatar size, whether access tokens are required).

## 3. Authenticate

### User Login

Most REST endpoints require authentication. Start by creating a session:

```bash
curl -X POST {{BASE_URL}}/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"example","password":"example"}'
```

On success, the response includes a session token. Use it in subsequent requests:

```bash
curl {{BASE_URL}}/users/@me \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

### Two-Factor Authentication

If the account has TOTP-based 2FA enabled, the login response will indicate a second step is required. Submit the TOTP code to complete authentication:

```bash
curl -X POST {{BASE_URL}}/auth/login/2fa \
  -H "Content-Type: application/json" \
  -d '{"session_token":"...","totp_code":"123456"}'
```

### Bot Authentication

Bot tokens are long-lived credentials created per-bot. Include them in the `Authorization` header:

```bash
curl {{BASE_URL}}/users/@me \
  -H "Authorization: Bot YOUR_BOT_TOKEN"
```

### Access Token Gate

Some deployments require an additional `X-API-Access-Token` header. Check `/capabilities` for `access_token_required`. See [Access Tokens](admin-access-tokens.md) for details.

## 4. Make Your First Requests

After authenticating, explore the API:

```bash
# Your user profile
curl {{BASE_URL}}/users/@me \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"

# Your user settings
curl {{BASE_URL}}/users/@me/settings \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"

# Your servers
curl {{BASE_URL}}/servers \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"

# Your notifications
curl {{BASE_URL}}/users/@me/notifications \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"

# Your feature flags and tier
curl {{BASE_URL}}/users/@me/features \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

## 5. Connect to the WebSocket Gateway

The gateway provides real-time events: new messages, presence updates, typing indicators, and voice signaling.

### Connection Flow

1. **Connect** to `{{WEBSOCKET_URL}}`
2. **Receive `HELLO`** — the server sends heartbeat interval and connection details
3. **Send `IDENTIFY`** — provide your session token to authenticate the connection
4. **Start heartbeating** — send heartbeat packets at the interval specified in `HELLO`
5. **Consume events** — receive `DISPATCH` events such as `READY`, `MESSAGE_CREATE`, `PRESENCE_UPDATE`

### Example (JavaScript)

```javascript
const ws = new WebSocket('ws://localhost:8000/gateway');

ws.onmessage = (event) => {
  const payload = JSON.parse(event.data);

  switch (payload.op) {
    case 10: // HELLO
      // Start heartbeating
      setInterval(() => {
        ws.send(JSON.stringify({ op: 1, d: null }));
      }, payload.d.heartbeat_interval);

      // Identify
      ws.send(JSON.stringify({
        op: 2,
        d: { token: 'YOUR_SESSION_TOKEN' }
      }));
      break;

    case 0: // DISPATCH
      console.log('Event:', payload.t, payload.d);
      break;

    case 11: // HEARTBEAT_ACK
      break;
  }
};
```

See [Gateway Connection](websocket/connection.md) for detailed payload formats and reconnect behavior.

### Gateway Configuration

The heartbeat interval is configured server-side:

```yaml
websocket:
  heartbeat_interval_ms: 45000   # 45 seconds
  session_timeout_ms: 60000       # 60 seconds before disconnect
  max_connections_per_user: 5     # concurrent WebSocket connections
```

See [WebSocket Configuration](config-websocket.md) for all gateway settings.

## 6. Understand Rate Limits

The server enforces rate limits at multiple levels:

| Tier | Default Limit | Scope |
|------|--------------|-------|
| Global | 100 req/60s | Entire server |
| Per-User | 120 req/60s | Per authenticated user |
| Per-IP | 60 req/60s | Per client IP |
| Bot | 1.5x user rate | Bot tokens |
| Admin | Bypass | Admin users |

When rate-limited, the server returns `429 Too Many Requests` with a `Retry-After` header. Always implement exponential backoff.

See [Rate Limits](rate-limits.md) for dynamic limits and [Rate Limiting Configuration](config-rate-limiting.md) for configuration details.

## 7. Choose Your Documentation Surface

Plexichat provides multiple documentation surfaces:

| Surface | Path | Best For |
|---------|------|----------|
| Narrative Docs | `/docs/api` | Guides, overviews, configuration |
| Swagger UI | `/docs` | Interactive API exploration |
| ReDoc | `/redoc` | Readable API reference |
| OpenAPI JSON | `/openapi.json` | Machine-readable schema |

Use this portal for conceptual guidance. Use Swagger/ReDoc for specific endpoint details.

## 8. Next Steps

- **[API Reference](api/index.md)** — Route-group overviews for every API module
- **[WebSocket Events](websocket/events.md)** — All gateway event types and payloads
- **[Configuration Overview](configuration.md)** — Setting up and customizing your server
- **[Security Best Practices](security.md)** — Authentication, encryption, and production security
- **[Performance Tuning](performance.md)** — Optimizing for your deployment scale
