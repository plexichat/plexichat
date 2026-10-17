# OAuth security module

This module implements secure OAuth 2.0 flows with server-side state, Proof Key for Code Exchange (PKCE) with `S256` only, and OpenID Connect nonce support. Use it to protect the authorization flow from CSRF, code interception, and replay attacks.

## Features

- Stores CSRF state server-side and marks each state as single-use
- Requires `code_challenge` with `code_challenge_method=S256` for PKCE flows (`src/api/routes/auth/oauth.py:122`); `plain` is rejected
- Generates and verifies PKCE verifiers and challenges (`S256` = `BASE64URL(SHA256(verifier))`, 43–128 characters per RFC 7636)
- Generates and verifies OpenID Connect nonces
- Enforces provider and `redirect_uri` binding against an exact-match allow-list
- Limits pending states per IP and cleans up expired states automatically
- Uses constant-time comparison for token and challenge checks
- Binds state to the browser with an `HttpOnly` cookie (`plexichat_oauth_state` scoped to `{api_prefix}/auth/oauth`)

## How it works

### State management

You store the state token on the server and verify it on callback:

1. You call `create_oauth_state()` to generate a random state token.
2. The module hashes the token with SHA-256 and stores the hash with metadata (`provider`, `redirect_uri`, `expires_at`, `nonce_hash`, `pkce_challenge`, `ip_address`, `cookie_issued`).
3. You include the raw `state_token` in the authorization URL.
4. On callback, you call `verify_oauth_state()` to look up the hash, check expiration, provider, and redirect URI, and mark the state as used with `UPDATE ... WHERE used = 0`.
5. Any reuse of the same state fails with `State token already used`.

### PKCE

PKCE prevents authorization code interception. The server never mints verifiers; the public client (SPA) creates the pair and the server verifies it.

1. The client generates a `code_verifier` (43–128 characters, `^[A-Za-z0-9\-._~]{43,128}$`).
2. The client creates `code_challenge = BASE64URL(SHA256(code_verifier))` and sends `code_challenge` with `code_challenge_method=S256` in the authorization request.
3. `GET /api/v1/auth/oauth/{provider}/login` rejects the request if `code_challenge` is missing, `code_challenge_method` is not `S256`, or the challenge does not match the regex. When `pkce_enabled` is `true` and the provider supports PKCE, `code_challenge` is required (`src/api/routes/auth/oauth.py:122`).
4. The client sends `code_verifier` with the token exchange (`POST /api/v1/auth/oauth/{provider}/callback`).
5. You verify the verifier with `verify_pkce(code_verifier, stored_challenge)` using `secrets.compare_digest`.

### Nonce (OpenID Connect)

For OIDC providers such as Google and Microsoft:

1. You set `include_nonce=True` when you create the state. The module generates a random nonce and stores its hash.
2. The provider includes the raw nonce in the ID token (`id_token` claim `nonce`).
3. You verify that the nonce in the ID token matches `state.nonce_value`. The callback decodes the JWT payload and rejects with `OAuth nonce mismatch` if it differs.

### Cookie binding (login-CSRF defense)

If you set `cookie_issued=True` when you create the state, the login endpoint sets `plexichat_oauth_state` as `HttpOnly`, `Secure`, `SameSite=Lax`, scoped to `{api_prefix}/auth/oauth`. The callback must present that cookie with `hmac.compare_digest` equality or the module rejects the flow with `state cookie mismatch`.

## Configure OAuth

Configure defaults in `config.yaml` under `oauth`:

| Key | Default | Description |
| --- | --- | --- |
| `state_ttl_seconds` | `600` | How long a state stays valid, in seconds (10 minutes). Keep this short. |
| `state_token_bytes` | `32` | Entropy for state tokens, in bytes. Use at least 32. |
| `nonce_token_bytes` | `32` | Entropy for OIDC nonces, in bytes. Use at least 32. |
| `cleanup_on_verify` | `true` | Whether `verify_oauth_state()` deletes expired states first. |
| `max_states_per_ip` | `10` | Maximum pending states per IP address. `0` means unlimited. |
| `state_cookie_required` | `true` | Whether to issue and require the state-binding cookie. |
| `pkce_enabled` | `true` | Whether PKCE is required for providers that support it. |
| `pkce.verifier_length` | `64` | Random bytes for the verifier before base64url encoding. Clamped to 32–96. |
| `pkce.min_verifier_length` | `43` | Minimum verifier length per RFC 7636. |
| `pkce.max_verifier_length` | `128` | Maximum verifier length per RFC 7636. |
| `oauth.allow_localhost_redirects` | `false` | Whether to allow `http://127.0.0.1:8000` and `http://localhost:8000` redirects. |
| `oauth.<provider>.allowed_redirect_uris` | `[]` | Exact-match allow-list for `redirect_uri` per provider. Required in production. |

Example:

```yaml
oauth:
  state_ttl_seconds: 600
  state_token_bytes: 32
  nonce_token_bytes: 32
  cleanup_on_verify: true
  max_states_per_ip: 10
  state_cookie_required: true
  pkce_enabled: true
  pkce:
    verifier_length: 64
    min_verifier_length: 43
    max_verifier_length: 128
  allow_localhost_redirects: false
  google:
    client_id: "YOUR_CLIENT_ID"
    client_secret: "YOUR_CLIENT_SECRET"
    allowed_redirect_uris: ["https://app.example.com/oauth/callback"]
  github:
    client_id: "YOUR_CLIENT_ID"
    client_secret: "YOUR_CLIENT_SECRET"
    allowed_redirect_uris: ["https://app.example.com/oauth/callback"]
  microsoft:
    client_id: "YOUR_CLIENT_ID"
    client_secret: "YOUR_CLIENT_SECRET"
    allowed_redirect_uris: ["https://app.example.com/oauth/callback"]
```

You can also pass a config dict when you create `OAuthStateManager(db, config)` or rely on the module singleton via `setup(db, config)`.

## Use OAuth state and PKCE

### 1. Set up the manager

```python
from src.core.auth.oauth import setup

oauth_mgr = setup(db, config={"state_ttl_seconds": 600})
# Or create directly:
from src.core.auth.oauth.state import OAuthStateManager
oauth_mgr = OAuthStateManager(db, config)
```

### 2. Create a state

Call `GET /api/v1/auth/oauth/{provider}/login` with required PKCE parameters. The endpoint validates `redirect_uri` against the allow-list, validates `code_challenge` (required, `S256` only, 43–128 chars), issues a state, and sets the binding cookie.

```python
from src.core.auth.oauth import create_oauth_state, generate_pkce_pair

# Client generates PKCE; server only verifies. For testing you can generate locally:
pkce = generate_pkce_pair()  # Returns PKCEChallenge with code_verifier and code_challenge (S256)

state = create_oauth_state(
    provider="google",
    redirect_uri="https://app.example.com/oauth/callback",
    include_nonce=True,  # Required for OIDC
    pkce_challenge=pkce.code_challenge,
    ip_address=request.client.host,
    cookie_issued=True,
)
if state is None:
    raise RuntimeError("Rate limited: too many pending states for this IP")

# Build the authorization URL
params = {
    "state": state.state_token,  # Send the raw token, only the hash is stored
    "code_challenge": pkce.code_challenge,
    "code_challenge_method": pkce.code_challenge_method,  # Always S256
    "nonce": state.nonce_value,  # For OIDC
}
```

Store `pkce.code_verifier` securely in the client until the token exchange. Do not store it in the state table — only the `code_challenge` is stored. The `code_verifier` is sent to `POST /api/v1/auth/oauth/{provider}/callback` as `code_verifier`.

Direct `POST /api/v1/auth/oauth/{provider}/login` without `code_challenge` returns `400 code_challenge is required for PKCE OAuth flows`. A `code_challenge_method` other than `S256` returns `400 Only the S256 code_challenge_method is supported`.

### 3. Verify the callback

```python
from src.core.auth.oauth import verify_oauth_state, verify_pkce

valid, state_record, error = verify_oauth_state(
    state_token=request.args.get("state"),
    provider="google",
    redirect_uri="https://app.example.com/oauth/callback",
)
if not valid:
    raise RuntimeError(error)  # "Invalid state token", "State token expired", etc.

# Verify cookie binding if state_record.cookie_issued is True (handled by the route)
# Verify PKCE if state_record.pkce_challenge is set:
if state_record.pkce_challenge:
    if not verify_pkce(code_verifier, state_record.pkce_challenge):
        raise RuntimeError("PKCE verification failed")

# Also verify state_record.nonce_value against the ID token nonce for OIDC.
```

The route `POST /api/v1/auth/oauth/{provider}/callback` performs all of the above, including cookie comparison with `hmac.compare_digest`, `code_verifier` presence check, `verify_pkce` with constant-time comparison, and `id_token.nonce` equality before exchanging the code.

### 4. Clean up expired states (optional)

```python
from src.core.auth.oauth import cleanup_expired_states

removed = cleanup_expired_states()
print(f"Removed {removed} expired states")
```

`verify_oauth_state()` already cleans up when `cleanup_on_verify` is `true`. Call this manually from a cron job if you disable that setting.

## Database schema

The module creates `auth_oauth_states`:

```sql
CREATE TABLE auth_oauth_states (
    id INTEGER PRIMARY KEY,
    state_hash TEXT NOT NULL UNIQUE,  -- SHA-256 of the state token
    provider TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    created_at INTEGER NOT NULL,      -- Milliseconds since epoch
    expires_at INTEGER NOT NULL,      -- Milliseconds since epoch
    used INTEGER DEFAULT 0,           -- 0 = pending, 1 = consumed
    nonce_hash TEXT,                  -- SHA-256 of the OIDC nonce, if any
    pkce_challenge TEXT,              -- BASE64URL(SHA256(verifier)), if any
    ip_address TEXT,                  -- For audit and rate limiting
    cookie_issued INTEGER DEFAULT 0   -- 1 if a binding cookie was issued
);
CREATE INDEX idx_oauth_states_expires ON auth_oauth_states(expires_at);
CREATE INDEX idx_oauth_states_hash ON auth_oauth_states(state_hash);
CREATE INDEX idx_oauth_states_ip ON auth_oauth_states(ip_address);
```

## Follow security best practices

- Keep `state_ttl_seconds` short (10 minutes). A short window limits replay.
- Treat every state as single-use. The module marks it used in an atomic `UPDATE ... WHERE used = 0` so race conditions fail safely.
- Bind the state to one `provider` and one `redirect_uri`. The module rejects mismatches and the route rejects `redirect_uri` values not on the exact-match allow-list.
- Generate high entropy: `state_token_bytes` and `nonce_token_bytes` of 32 bytes give 256 bits.
- Use `S256` only for PKCE. The module uses `secrets.compare_digest` for challenge comparison. `plain` is not supported.
- Enable `cookie_issued` when you can set a state-binding `HttpOnly` cookie. Require that cookie on callback.
- Enforce `max_states_per_ip` to prevent state-exhaustion from one IP.
- Always send `code_challenge` from the client; the server never generates the verifier for you.

## Test the module

Run the OAuth security tests:

```bash
pytest src/tests/auth/test_oauth_security.py -v
```
