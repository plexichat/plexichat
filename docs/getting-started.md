# Get started

This guide walks you through connecting to a Plexichat server, authenticating, and making your first API requests. Whether you build a client, a bot, or an integration, start here.

## 1. Identify your server endpoints

Before you make any requests, collect three URLs from your server administrator:

*   **REST API:** `http://localhost:8000/api/v1` — All REST operations. The prefix is `api.api_prefix` (`src/config_defaults.py:133`).
*   **WebSocket gateway:** `ws://localhost:8000/gateway` — Real-time events.
*   **Documentation:** `http://localhost:8000/docs/api` — This portal. The path is `docs.path` (default `/docs/api`).

The server also exposes interactive docs at `/docs` (Swagger UI), `/redoc` (ReDoc), and `/openapi.json` (raw OpenAPI spec).

## 2. Check server compatibility

Verify the server version and capabilities before you rely on feature-specific behavior:

```bash
# Get the server version and minimum supported client version
curl http://localhost:8000/api/v1/version

# Negotiate compatibility for your client version (format: a.1.0-145)
curl -X POST http://localhost:8000/api/v1/version/negotiate \
  -H "Content-Type: application/json" \
  -d '{"client_version":"a.1.0-145"}'

# Discover public capabilities without authentication
curl http://localhost:8000/api/v1/capabilities

# Get server status and uptime
curl http://localhost:8000/api/v1/status
```

The version format is `[stage].[major].[minor]-[build]` where stage is `a` (alpha), `b` (beta), `c` (candidate), or `r` (release). For example, `a.1.0-145` (`src/utils/common_utils/utils/version/core.py:60`, `src/server/lifecycle.py:16`). The `POST /api/v1/version/negotiate` endpoint (`src/api/routes/version.py:94`) returns `compatible`, `update_required`, and `update_recommended` fields. It returns `426 Upgrade Required` when the client version is below the minimum. Use `/capabilities` to adapt your client to server configuration such as maximum avatar size and whether the access-token gate is enabled.

## 3. Authenticate

Most REST endpoints require authentication.

### User login with username and password

Create a session and then use the session token in subsequent requests:

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"example","password":"example"}'

# Use the returned token
curl http://localhost:8000/api/v1/users/@me \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

### OAuth login

When the server has OAuth providers configured (`oauth.google`, `oauth.github`, `oauth.microsoft`, `oauth.gitlab` in `src/config_defaults.py:460`), users can authenticate with an external account. The supported providers are defined in `src/api/routes/auth/oauth_config.py:1`: `google`, `github`, `microsoft`, and `gitlab`.

OAuth uses PKCE with `S256` and a short-lived, `HttpOnly` state cookie that binds the flow to the browser that started it.

**Step 1: Initiate the login**

Call `GET /api/v1/auth/oauth/{provider}/login` with the required query parameters (`src/api/routes/auth/oauth.py:111`):

```bash
# Initiate Google login (PKCE S256 is required)
curl "http://localhost:8000/api/v1/auth/oauth/google/login?redirect_uri=https://app.example.com/oauth-callback&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM&code_challenge_method=S256"

# Initiate GitHub login (PKCE is not used for GitHub)
curl "http://localhost:8000/api/v1/auth/oauth/github/login?redirect_uri=https://app.example.com/oauth-callback"
```

| Parameter | Required | Description |
| --- | --- | --- |
| `redirect_uri` | Yes | Must match an entry in `oauth.<provider>.allowed_redirect_uris`. The check is an exact match (`src/api/routes/auth/oauth.py:68`). |
| `code_challenge` | Yes for PKCE providers | `43–128` characters matching `^[A-Za-z0-9\-._~]{43,128}$`. Required when `oauth.pkce_enabled` is `true` and the provider supports PKCE. |
| `code_challenge_method` | No | Only `S256` is supported. |

The response contains `url` (the provider authorization URL) and `state`. The server also sets the `plexichat_oauth_state` cookie (`src/api/routes/auth/oauth.py:91`) when `oauth.state_cookie_required` is `true` (the default).

**Step 2: Redirect the user**

Redirect the user to the `url` from the previous response. The user authorizes at the provider, and the provider redirects back to your `redirect_uri` with an authorization `code`.

**Step 3: Complete the callback**

Exchange the code for a session by calling `POST /api/v1/auth/oauth/{provider}/callback` (`src/api/routes/auth/oauth.py:304`):

```bash
curl -X POST http://localhost:8000/api/v1/auth/oauth/microsoft/callback \
  -H "Content-Type: application/json" \
  -d '{
    "code": "AUTHORIZATION_CODE",
    "state": "STATE_FROM_STEP_1",
    "redirect_uri": "https://app.example.com/oauth-callback",
    "code_verifier": "ORIGINAL_PKCE_VERIFIER"
  }'
```

The server verifies the state, the `state` cookie, the PKCE verifier, and the `redirect_uri` allow-list before it exchanges the code with the provider. If the OAuth account matches an existing user, the user is logged in. Otherwise, the server creates a new account unless `allow_registration: false` is set.

The callback returns a session token. Use it the same way as the username and password flow:

```bash
curl http://localhost:8000/api/v1/users/@me \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

**OAuth server configuration:**

```yaml
oauth:
  # PKCE and state settings
  pkce_enabled: true
  state_cookie_required: true
  state_ttl_seconds: 600
  # Optional: allow localhost redirects for development
  allow_localhost_redirects: false
  google:
    client_id: "your-google-client-id"
    client_secret: "your-google-client-secret"
    allowed_redirect_uris:
      - "https://app.example.com/oauth-callback"
  github:
    client_id: "your-github-client-id"
    client_secret: "your-github-client-secret"
    allowed_redirect_uris:
      - "https://app.example.com/oauth-callback"
  microsoft:
    client_id: "your-microsoft-client-id"
    client_secret: "your-microsoft-client-secret"
    allowed_redirect_uris:
      - "https://app.example.com/oauth-callback"
  gitlab:
    client_id: "your-gitlab-client-id"
    client_secret: "your-gitlab-client-secret"
    allowed_redirect_uris:
      - "https://app.example.com/oauth-callback"
```

OAuth is optional. When no providers are configured, only username and password login is available.

### Two-factor authentication

When an account has TOTP-based 2FA enabled, the login response indicates that a second step is required. Submit the TOTP code to complete authentication:

```bash
curl -X POST http://localhost:8000/api/v1/auth/login/2fa \
  -H "Content-Type: application/json" \
  -d '{"session_token":"...","totp_code":"123456"}'
```

### Bot authentication

Bot tokens are long-lived credentials that you create per bot. Include them with the `Bot` scheme:

```bash
curl http://localhost:8000/api/v1/users/@me \
  -H "Authorization: Bot YOUR_BOT_TOKEN"
```

### Access-token gate

Some deployments require an additional `X-API-Access-Token` header. Check `/capabilities` for `access_token_required`. See [Access Tokens](deployment/configuration/config-api.md) for details.

## 4. Make your first requests

After you authenticate, explore the API:

```bash
# Your user profile
curl http://localhost:8000/api/v1/users/@me \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"

# Your user settings
curl http://localhost:8000/api/v1/users/@me/settings \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"

# Your servers
curl http://localhost:8000/api/v1/servers \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"

# Your notifications
curl http://localhost:8000/api/v1/users/@me/notifications \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"

# Your feature flags and tier
curl http://localhost:8000/api/v1/users/@me/features \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

## 5. Connect to the WebSocket gateway

The gateway delivers real-time events such as new messages, presence updates, typing indicators, and voice signaling.

### Connection flow

1.  Connect to `ws://localhost:8000/gateway`.
2.  Receive `HELLO` (opcode `10`) with the heartbeat interval.
3.  Send `IDENTIFY` (opcode `2`) with your session token.
4.  Send heartbeat packets (opcode `1`) at the interval from `HELLO`.
5.  Consume `DISPATCH` events (opcode `0`) such as `READY`, `MESSAGE_CREATE`, and `PRESENCE_UPDATE`.

### Example

```javascript
const ws = new WebSocket('ws://localhost:8000/gateway');

ws.onmessage = (event) => {
  const payload = JSON.parse(event.data);

  switch (payload.op) {
    case 10: // HELLO
      // Start heartbeating at the requested interval
      setInterval(() => {
        ws.send(JSON.stringify({ op: 1, d: null }));
      }, payload.d.heartbeat_interval);

      // Identify with the session token
      ws.send(JSON.stringify({
        op: 2,
        d: { token: 'YOUR_SESSION_TOKEN' },
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

See [Gateway Connection](websocket/connection.md) for payload formats and reconnect behavior.

### Gateway configuration

Configure the heartbeat interval on the server side:

```yaml
websocket:
  heartbeat_interval_ms: 45000   # 45 seconds
  session_timeout_ms: 60000       # 60 seconds before disconnect
  max_connections_per_user: 5     # Concurrent WebSocket connections
```

See [WebSocket Configuration](deployment/configuration/config-websocket.md) for all gateway settings.

## 6. Understand rate limits

The server enforces rate limits at multiple levels. Defaults are defined in `src/config_defaults.py:270`.

| Scope | Requests per 60 s | Burst | Hourly limit | Daily limit |
| --- | --- | --- | --- | --- |
| Global | 25000 | 5000 | — | — |
| Per-user | 10000 | 2500 | 300000 | 5000000 |
| Per-IP | 5000 | 1500 | 150000 | 1000000 |

Additional factors:

*   **Bot multiplier:** `1.5` — Bots receive 1.5× the per-user limit.
*   **Webhook multiplier:** `1.0` — Webhooks use the same limit as users by default.
*   **Admin bypass:** Admin users bypass per-user limits when `rate_limiting.admin_bypass` is `true`. Global and IP limits still apply.
*   **Static client routes:** `static_client_html` allows 300 requests per 60 s (burst 100, hourly 6000) and `static_client_assets` allows 5000 requests per 60 s (burst 1000, hourly 180000).

When you exceed a limit, the server returns `429 Too Many Requests` with a `Retry-After` header. Implement exponential backoff when you handle this response.

See [Rate Limits](rate-limits.md) and [Rate Limiting Configuration](deployment/configuration/config-rate-limiting.md) for details and tuning guidance.

## 7. Choose your documentation surface

Plexichat provides multiple documentation surfaces:

*   **Narrative docs:** `/docs/api` — Guides, overviews, and configuration.
*   **Swagger UI:** `/docs` — Interactive API exploration.
*   **ReDoc:** `/redoc` — Readable API reference.
*   **OpenAPI JSON:** `/openapi.json` — Machine-readable schema.

Use this portal for conceptual guidance. Use Swagger or ReDoc for endpoint-specific details.

## 8. Next steps

*   [API Reference](api/index.md) — Route-group overviews for every API module.
*   [WebSocket Events](websocket/events.md) — Gateway event payloads and types.
*   [Configuration Overview](configuration.md) — Set up and customize your server.
*   [Security Best Practices](security.md) — Authentication, encryption, and production security.
*   [Performance Tuning](performance.md) — Optimize for your deployment scale.

## Encrypted columns at rest

Medium-sensitivity user data fields are stored as AES-256-GCM ciphertext through the Plexichat system keyring. You see paired `*_encrypted` columns alongside legacy plaintext columns in user-related schemas, for example alongside `email`, `phone`, `display_name`, and `bio`. The `*_encrypted` columns are the source of truth. The plaintext columns remain for backward compatibility with older clients and migration tooling and are populated transparently on read. Message content is encrypted at rest when `messaging.encrypt_messages: true` (the default) through the dedicated message keyring. See [Keyrings](keyrings.md) and [Security Best Practices](security.md) for the keyring layout, KEK precedence, and rotation procedures.
