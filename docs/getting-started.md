# Getting Started

This guide walks you through connecting to a Plexichat server, authenticating, and making your first API requests. Whether you're building a client, a bot, or an integration, start here.

## 1. Identify Your Server Endpoints

Before making any requests, you need three URLs:

- REST API: `http://localhost:8000/api/v1` -- all REST operations
- WebSocket Gateway: `ws://localhost:8000/gateway` -- real-time events
- Documentation: `http://localhost:8000/docs/api` -- this portal

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

### User Login (Username/Password)

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

### OAuth Login (Google, GitHub, Microsoft)

If the server has OAuth providers configured (see `oauth.google`, `oauth.github`, `oauth.microsoft` in config), users can authenticate via their external account:

**Step 1: Redirect the user to the OAuth authorize URL**

```bash
# Google
GET {{BASE_URL}}/auth/oauth/google

# GitHub
GET {{BASE_URL}}/auth/oauth/github

# Microsoft
GET {{BASE_URL}}/auth/oauth/microsoft
```

This redirects the user to the provider's consent screen. After the user authorizes, the provider redirects back to Plexichat with an authorization code.

**Step 2: Plexichat exchanges the code for user info**

The server handles the code exchange automatically. If the OAuth account matches an existing user, the user is logged in. If not, a new account is created (unless `allow_registration: false` is set).

**Step 3: Receive session token**

The OAuth callback returns a session token just like username/password login. Use it in subsequent requests:

```bash
curl {{BASE_URL}}/users/@me \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

**OAuth configuration requirements** (server-side):

```yaml
oauth:
  google:
    client_id: "your-google-client-id"
    client_secret: "your-google-client-secret"
  github:
    client_id: "your-github-client-id"
    client_secret: "your-github-client-secret"
  microsoft:
    client_id: "your-microsoft-client-id"
    client_secret: "your-microsoft-client-secret"
```

OAuth is optional. If no providers are configured, only username/password login is available.

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

- Global: 100 req/60s (entire server)
- Per-User: 120 req/60s (per authenticated user)
- Per-IP: 60 req/60s (per client IP)
- Bot: 1.5x user rate (bot tokens)
- Admin: bypass (admin users)

When rate-limited, the server returns `429 Too Many Requests` with a `Retry-After` header. Always implement exponential backoff.

See [Rate Limits](rate-limits.md) for dynamic limits and [Rate Limiting Configuration](config-rate-limiting.md) for configuration details.

## 7. Choose Your Documentation Surface

Plexichat provides multiple documentation surfaces:

- Narrative Docs: `/docs/api` -- guides, overviews, configuration
- Swagger UI: `/docs` -- interactive API exploration
- ReDoc: `/redoc` -- readable API reference
- OpenAPI JSON: `/openapi.json` -- machine-readable schema

Use this portal for conceptual guidance. Use Swagger/ReDoc for specific endpoint details.

## 8. Next Steps

- **[API Reference](api/index.md)** — Route-group overviews for every API module
- **[WebSocket Events](websocket/events.md)** — All gateway event types and payloads
- **[Configuration Overview](configuration.md)** — Setting up and customizing your server
- **[Security Best Practices](security.md)** — Authentication, encryption, and production security
- **[Performance Tuning](performance.md)** — Optimizing for your deployment scale
