# OAuth Security Module

This module provides secure OAuth2 authentication flows with server-side state management and PKCE support.

## Features

- **Server-side State Storage**: CSRF protection tokens are stored in the database, not just returned to the client
- **PKCE Support**: Proof Key for Code Exchange prevents authorization code interception attacks
- **Nonce Support**: OpenID Connect nonce for replay attack prevention
- **Automatic Cleanup**: Expired states are automatically removed
- **Single-use States**: Each state token can only be used once

## Security Model

### State Management

OAuth state tokens prevent CSRF attacks during the authorization flow:

1. When initiating OAuth login, a random state token is generated
2. The state hash is stored server-side with metadata (provider, redirect_uri, expiry)
3. The state token is included in the authorization URL
4. On callback, the state is verified against the stored record
5. The state is marked as used and cannot be reused

### PKCE (Proof Key for Code Exchange)

PKCE prevents authorization code interception, especially important for public clients:

1. Generate a random `code_verifier` (43-128 chars)
2. Create `code_challenge = BASE64URL(SHA256(code_verifier))`
3. Send `code_challenge` with authorization request
4. Send `code_verifier` with token exchange
5. Provider verifies `SHA256(code_verifier)` matches `code_challenge`

### Nonce (OpenID Connect)

For OIDC providers (Google, Microsoft), a nonce prevents token replay:

1. Generate random nonce and include in authorization request
2. Provider includes nonce in the ID token
3. Verify nonce in ID token matches the one we sent

## Configuration

OAuth configuration in `config.yaml`:

```yaml
oauth:
  # State token TTL in seconds (default: 600 = 10 minutes)
  state_ttl_seconds: 600
  
  # Entropy for state tokens in bytes (minimum 32 recommended)
  state_token_bytes: 32
  
  # Entropy for OIDC nonce in bytes (minimum 32 recommended)
  nonce_token_bytes: 32
  
  # Clean up expired states on each verification (recommended: true)
  cleanup_on_verify: true
  
  # Maximum pending OAuth states per IP address (0 = unlimited)
  max_states_per_ip: 10
  
  # Enable PKCE for all providers (recommended)
  pkce_enabled: true
  
  # PKCE configuration
  pkce:
    # Length of random bytes for code verifier (32-96, default 64)
    verifier_length: 64
    # Minimum verifier length per RFC 7636 (do not change unless required)
    min_verifier_length: 43
    # Maximum verifier length per RFC 7636 (do not change unless required)
    max_verifier_length: 128
  
  # Provider configurations
  google:
    client_id: "YOUR_CLIENT_ID"
    client_secret: "YOUR_CLIENT_SECRET"
  
  github:
    client_id: "YOUR_CLIENT_ID"
    client_secret: "YOUR_CLIENT_SECRET"
  
  microsoft:
    client_id: "YOUR_CLIENT_ID"
    client_secret: "YOUR_CLIENT_SECRET"
```

## Usage

### Creating OAuth State

```python
from src.core.auth.oauth import create_oauth_state, generate_pkce_pair

# Generate PKCE pair
pkce = generate_pkce_pair()

# Create state with PKCE
state = create_oauth_state(
    provider="google",
    redirect_uri="https://example.com/callback",
    include_nonce=True,  # For OIDC providers
    pkce_challenge=pkce.code_challenge,
    ip_address=request.client.host,
)

# Include in authorization URL
params = {
    "state": state.state_token,
    "code_challenge": pkce.code_challenge,
    "code_challenge_method": "S256",
    "nonce": state.nonce_value,  # For OIDC
}
```

### Verifying OAuth Callback

```python
from src.core.auth.oauth import verify_oauth_state, verify_pkce

# Verify state from callback
valid, state_record, error = verify_oauth_state(
    state_token=request.args.get("state"),
    provider="google",
    redirect_uri="https://example.com/callback",
)

if not valid:
    raise HTTPException(400, error)

# Verify PKCE during token exchange
if state_record.pkce_challenge:
    if not verify_pkce(code_verifier, state_record.pkce_challenge):
        raise HTTPException(400, "PKCE verification failed")
```

## Database Schema

The module creates an `auth_oauth_states` table:

```sql
CREATE TABLE auth_oauth_states (
    id INTEGER PRIMARY KEY,
    state_hash TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used INTEGER DEFAULT 0,
    nonce_hash TEXT,
    pkce_challenge TEXT,
    ip_address TEXT
);
```

## Security Considerations

1. **State TTL**: Keep short (10 minutes default) to limit attack window
2. **Single Use**: States are marked used immediately on verification
3. **Provider Binding**: States are bound to specific provider
4. **Redirect URI Binding**: States are bound to specific redirect URI
5. **Constant-time Comparison**: All token comparisons use constant-time algorithms
6. **High Entropy**: State tokens use 32 bytes of cryptographic randomness

## Testing

Run OAuth security tests:

```bash
pytest src/tests/auth/test_oauth_security.py -v
```
