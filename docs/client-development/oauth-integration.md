# OAuth Integration Guide

This guide covers integrating OAuth 2.0 with Plexichat for third-party login and bot authorization.

## Overview

Plexichat supports OAuth 2.0 authorization code grant with PKCE (Proof Key for Code Exchange) for secure authentication. This enables:
- Third-party login (Google, GitHub, Microsoft, etc.)
- Bot authorization (add to server flows)
- Application access tokens

## OAuth 2.0 Flow

### Authorization Code Grant with PKCE

The PKCE flow prevents authorization code interception attacks, especially important for public clients (SPAs, mobile apps).

### Flow Diagram

```
+--------+                               +---------------+
|        |                               |               |
|        |--> (1) Auth Request ---------->|               |
| Client |    + code_challenge          | Authorization |
|        |    + state                  |    Server     |
|        |<-- (2) Auth Code ------------|               |
|        |    + state                  |               |
+--------+                               +---------------+
    |
    v
+--------+                               +---------------+
|        |                               |               |
|        |--> (3) Token Request --------->|               |
| Client |    + code                     |  Token Server  |
|        |    + code_verifier            |               |
|        |<-- (4) Access Token ---------|               |
|        |    + refresh_token (optional) |               |
+--------+                               +---------------+
```

### Step 1: Authorization Request

Redirect user to authorization endpoint:

```
GET /oauth2/authorize?
  client_id=your_application_id&
  redirect_uri=https://your-app.com/callback&
  response_type=code&
  scope=identify%20email&
  state=random_state_string&
  code_challenge=your_code_challenge&
  code_challenge_method=S256
```

**Parameters:**
- `client_id`: Your application ID
- `redirect_uri`: Registered redirect URI
- `response_type`: Always "code"
- `scope`: Requested scopes (space-separated)
- `state`: Random string for CSRF protection
- `code_challenge`: PKCE challenge (BASE64URL(SHA256(code_verifier)))
- `code_challenge_method`: Always "S256"

### Step 2: User Authorization

User sees authorization screen:
- Application name and description
- Requested permissions (scopes)
- Authorize or Cancel buttons

If user authorizes, redirect to `redirect_uri` with authorization code:

```
https://your-app.com/callback?
  code=authorization_code&
  state=random_state_string
```

### Step 3: Token Exchange

Exchange authorization code for access token:

```http
POST /oauth2/token
Content-Type: application/json

{
  "client_id": "your_application_id",
  "client_secret": "your_client_secret",
  "grant_type": "authorization_code",
  "code": "authorization_code",
  "redirect_uri": "https://your-app.com/callback",
  "code_verifier": "your_code_verifier"
}
```

**Parameters:**
- `client_id`: Your application ID
- `client_secret`: Your application secret (confidential clients only)
- `grant_type`: Always "authorization_code"
- `code`: Authorization code from callback
- `redirect_uri`: Must match authorization request
- `code_verifier`: PKCE verifier used to generate challenge

Response:
```json
{
  "access_token": "access_token_here",
  "token_type": "Bearer",
  "expires_in": 604800,
  "refresh_token": "refresh_token_here",
  "scope": "identify email"
}
```

### Step 4: Use Access Token

Include access token in API requests:

```http
Authorization: Bearer access_token_here
```

## PKCE Implementation

### Generate PKCE Pair

```javascript
async function generatePKCEPair() {
  // Generate random code verifier (43-128 characters)
  const array = new Uint8Array(32);
  window.crypto.getRandomValues(array);
  const codeVerifier = base64UrlEncode(array);

  // Generate code challenge
  const encoder = new TextEncoder();
  const data = encoder.encode(codeVerifier);
  const hash = await window.crypto.subtle.digest('SHA-256', data);
  const codeChallenge = base64UrlEncode(hash);

  return { codeVerifier, codeChallenge };
}

function base64UrlEncode(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary)
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=/g, '');
}
```

### Python Implementation

```python
import os
import base64
import hashlib
import secrets

def generate_pkce_pair():
    # Generate random code verifier
    verifier = secrets.token_urlsafe(64)

    # Generate code challenge
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip('=')

    return verifier, challenge
```

## State Management

State tokens prevent CSRF attacks during OAuth flow.

### Generate State Token

```javascript
function generateState() {
  const array = new Uint8Array(32);
  window.crypto.getRandomValues(array);
  return base64UrlEncode(array);
}
```

### Store and Verify State

```javascript
// Store state before redirect
const state = generateState();
sessionStorage.setItem('oauth_state', state);

// Verify state on callback
const returnedState = new URLSearchParams(window.location.search).get('state');
const storedState = sessionStorage.getItem('oauth_state');

if (returnedState !== storedState) {
  throw new Error('Invalid state - possible CSRF attack');
}

sessionStorage.removeItem('oauth_state');
```

## OAuth Scopes

Scopes define what permissions your application requests.

### Available Scopes

| Scope | Description |
|-------|-------------|
| `identify` | Read basic user information (username, avatar) |
| `email` | Read user email address |
| `guilds` | Read servers user is a member of |
| `guilds.join` | Join servers on behalf of user |
| `bot` | Bot authorization (add to server) |
| `connections` | Read user connections (Twitch, YouTube, etc.) |
| `messages.read` | Read user messages |
| `webhook.incoming` | Manage webhooks |

### Scope Request

Include scopes in authorization request:

```
scope=identify%20email%20guilds
```

### Bot Authorization

For bot authorization, include `bot` scope and `permissions` parameter:

```
/oauth2/authorize?
  client_id=your_application_id&
  redirect_uri=https://your-app.com/callback&
  response_type=code&
  scope=bot&
  permissions=8&
  state=random_state_string
```

Permissions bitmask calculation:

```javascript
const Permissions = {
  CREATE_INSTANT_INVITE: 1 << 0,
  KICK_MEMBERS: 1 << 1,
  BAN_MEMBERS: 1 << 2,
  ADMINISTRATOR: 1 << 3,
  MANAGE_CHANNELS: 1 << 4,
  MANAGE_GUILD: 1 << 5,
  ADD_REACTIONS: 1 << 6,
  VIEW_AUDIT_LOG: 1 << 7,
  PRIORITY_SPEAKER: 1 << 8,
  STREAM: 1 << 9,
  READ_MESSAGES: 1 << 10,
  SEND_MESSAGES: 1 << 11,
  SEND_TTS_MESSAGES: 1 << 12,
  MANAGE_MESSAGES: 1 << 13,
  EMBED_LINKS: 1 << 14,
  ATTACH_FILES: 1 << 15,
  READ_MESSAGE_HISTORY: 1 << 16,
  MENTION_EVERYONE: 1 << 17,
  EXTERNAL_EMOJIS: 1 << 18,
  VIEW_GUILD_INSIGHTS: 1 << 19,
  CONNECT: 1 << 20,
  SPEAK: 1 << 21,
  MUTE_MEMBERS: 1 << 22,
  DEAFEN_MEMBERS: 1 << 23,
  MOVE_MEMBERS: 1 << 24,
  USE_VAD: 1 << 25,
  CHANGE_NICKNAME: 1 << 26,
  MANAGE_NICKNAMES: 1 << 27,
  MANAGE_ROLES: 1 << 28,
  MANAGE_WEBHOOKS: 1 << 29,
  MANAGE_EMOJIS: 1 << 30
};

// Example: Send messages + read messages
const permissions = Permissions.SEND_MESSAGES | Permissions.READ_MESSAGES;
```

## Token Refresh

Access tokens expire (default 7 days). Use refresh token to obtain new access token without user interaction.

### Refresh Token Request

```http
POST /oauth2/token
Content-Type: application/json

{
  "client_id": "your_application_id",
  "client_secret": "your_client_secret",
  "grant_type": "refresh_token",
  "refresh_token": "refresh_token_here"
}
```

Response:
```json
{
  "access_token": "new_access_token_here",
  "token_type": "Bearer",
  "expires_in": 604800,
  "refresh_token": "new_refresh_token_here",
  "scope": "identify email"
}
```

### Token Refresh Implementation

```javascript
async function refreshAccessToken(refreshToken) {
  const response = await fetch('/oauth2/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      client_id: clientId,
      client_secret: clientSecret,
      grant_type: 'refresh_token',
      refresh_token: refreshToken
    })
  });

  const data = await response.json();

  // Store new tokens
  localStorage.setItem('access_token', data.access_token);
  localStorage.setItem('refresh_token', data.refresh_token);

  return data.access_token;
}
```

### Automatic Token Refresh

```javascript
async function apiRequest(url, options = {}) {
  let accessToken = localStorage.getItem('access_token');

  const response = await fetch(url, {
    ...options,
    headers: {
      ...options.headers,
      'Authorization': `Bearer ${accessToken}`
    }
  });

  if (response.status === 401) {
    // Token expired, refresh
    const refreshToken = localStorage.getItem('refresh_token');
    accessToken = await refreshAccessToken(refreshToken);

    // Retry request with new token
    return fetch(url, {
      ...options,
      headers: {
        ...options.headers,
        'Authorization': `Bearer ${accessToken}`
      }
    });
  }

  return response;
}
```

## Third-Party Provider Integration

Plexichat supports OAuth with third-party providers (Google, GitHub, Microsoft).

### Google OAuth

**Setup:**
1. Create OAuth 2.0 client in Google Cloud Console
2. Add authorized redirect URI: `https://your-server.com/oauth2/google/callback`
3. Copy client ID and client secret

**Server Configuration:**
```yaml
# config/config.yaml
oauth:
  google:
    client_id: "your_google_client_id"
    client_secret: "your_google_client_secret"
```

**Authorization URL:**
```
https://accounts.google.com/o/oauth2/v2/auth?
  client_id=your_google_client_id&
  redirect_uri=https://your-server.com/oauth2/google/callback&
  response_type=code&
  scope=openid%20profile%20email&
  state=random_state_string
```

### GitHub OAuth

**Setup:**
1. Create OAuth App in GitHub Developer Settings
2. Add authorization callback URL: `https://your-server.com/oauth2/github/callback`
3. Copy client ID and client secret

**Server Configuration:**
```yaml
oauth:
  github:
    client_id: "your_github_client_id"
    client_secret: "your_github_client_secret"
```

**Authorization URL:**
```
https://github.com/login/oauth/authorize?
  client_id=your_github_client_id&
  redirect_uri=https://your-server.com/oauth2/github/callback&
  scope=user:email&
  state=random_state_string
```

### Microsoft OAuth

**Setup:**
1. Register app in Azure Portal
2. Add redirect URI: `https://your-server.com/oauth2/microsoft/callback`
3. Copy application ID and client secret

**Server Configuration:**
```yaml
oauth:
  microsoft:
    client_id: "your_microsoft_client_id"
    client_secret: "your_microsoft_client_secret"
```

**Authorization URL:**
```
https://login.microsoftonline.com/common/oauth2/v2.0/authorize?
  client_id=your_microsoft_client_id&
  redirect_uri=https://your-server.com/oauth2/microsoft/callback&
  response_type=code&
  scope=openid%20profile%20email&
  state=random_state_string
```

## Custom OAuth Provider

To add a custom OAuth provider:

### 1. Implement Provider Handler

```python
from src.core.auth.oauth import OAuthStateManager

class CustomOAuthProvider:
    def __init__(self, db, config):
        self.db = db
        self.config = config
        self.state_manager = OAuthStateManager(db, config)

    def get_authorization_url(self, redirect_uri, state):
        client_id = self.config.get('client_id')
        return (
            f"https://auth.example.com/authorize?"
            f"client_id={client_id}&"
            f"redirect_uri={redirect_uri}&"
            f"response_type=code&"
            f"state={state}"
        )

    async def exchange_code(self, code, redirect_uri):
        client_id = self.config.get('client_id')
        client_secret = self.config.get('client_secret')

        async with aiohttp.ClientSession() as session:
            async with session.post(
                'https://auth.example.com/token',
                json={
                    'client_id': client_id,
                    'client_secret': client_secret,
                    'code': code,
                    'grant_type': 'authorization_code',
                    'redirect_uri': redirect_uri
                }
            ) as response:
                data = await response.json()
                return data['access_token']

    async def get_user_info(self, access_token):
        async with aiohttp.ClientSession() as session:
            async with session.get(
                'https://api.example.com/user',
                headers={'Authorization': f'Bearer {access_token}'}
            ) as response:
                return await response.json()
```

### 2. Add API Routes

```python
from fastapi import APIRouter, Request, HTTPException

router = APIRouter()

@router.get('/oauth2/custom/authorize')
async def custom_authorize(request: Request):
    redirect_uri = f"{request.url.scheme}://{request.url.netloc}/oauth2/custom/callback"
    state = create_oauth_state('custom', redirect_uri)

    auth_url = custom_provider.get_authorization_url(redirect_uri, state.state_token)

    return RedirectResponse(auth_url)

@router.get('/oauth2/custom/callback')
async def custom_callback(request: Request):
    code = request.query_params.get('code')
    state = request.query_params.get('state')

    valid, state_record, error = verify_oauth_state(state, 'custom')
    if not valid:
        raise HTTPException(400, error)

    access_token = await custom_provider.exchange_code(code, state_record.redirect_uri)
    user_info = await custom_provider.get_user_info(access_token)

    # Create or link user account
    # ...

    return RedirectResponse('/app')
```

### 3. Register Provider

```python
# In main.py
from src.core.auth.oauth import custom_provider

custom_provider = CustomOAuthProvider(db, config.get('oauth', {}).get('custom', {}))
app.include_router(custom_provider.router, prefix='/oauth2/custom')
```

## Security Considerations

### State Token Security

- Use cryptographically secure random values (32+ bytes)
- Store state server-side with short TTL (10 minutes)
- Verify state on callback
- Mark state as used after verification (single-use)

### PKCE Security

- Use SHA-256 for code challenge (S256 method)
- Generate code verifier with sufficient entropy (32+ bytes)
- Keep code verifier secret until token exchange
- Never include code verifier in authorization URL

### Client Secret Security

- Never expose client secret in client-side code
- Use environment variables for secrets
- Rotate client secrets periodically
- Use different secrets for development and production

### Redirect URI Validation

- Register all valid redirect URIs
- Validate redirect URI on callback
- Use HTTPS for production redirect URIs
- Prevent open redirect vulnerabilities

### Token Storage

- Store access tokens securely (httpOnly cookies, secure storage)
- Encrypt tokens at rest
- Implement token expiration handling
- Use refresh tokens to minimize user re-authentication

## Error Handling

### Common OAuth Errors

**invalid_client:**
- Client ID or secret is invalid
- Check application credentials

**invalid_grant:**
- Authorization code is invalid or expired
- Authorization code already used
- Redirect URI mismatch

**invalid_scope:**
- Requested scope is invalid
- Scope not authorized for application

**invalid_redirect_uri:**
- Redirect URI not registered
- Redirect URI mismatch

**access_denied:**
- User denied authorization
- User cancelled authorization flow

### Error Response Format

```json
{
  "error": "invalid_grant",
  "error_description": "Authorization code has expired"
}
```

### Error Handling Implementation

```javascript
async function handleOAuthCallback(code, state) {
  try {
    const tokens = await exchangeCodeForTokens(code, state);
    return tokens;
  } catch (error) {
    if (error.error === 'invalid_grant') {
      // Authorization code expired, restart flow
      window.location.href = '/login';
    } else if (error.error === 'access_denied') {
      // User denied authorization
      window.location.href = '/login?denied=true';
    } else {
      // Other error
      console.error('OAuth error:', error);
      window.location.href = '/login?error=true';
    }
  }
}
```

## Testing

### Local Testing

Test OAuth flow locally:

1. Register localhost redirect URI:
   ```
   http://localhost:3000/callback
   ```

2. Use test application credentials

3. Test full flow:
   - Authorization request
   - User authorization
   - Code exchange
   - Token usage

### Test Scenarios

- Successful authorization
- User denies authorization
- Invalid state (CSRF attempt)
- Expired authorization code
- Invalid redirect URI
- Invalid client credentials
- Token refresh

## Resources

- [OAuth 2.0 RFC 6749](https://tools.ietf.org/html/rfc6749)
- [PKCE RFC 7636](https://tools.ietf.org/html/rfc7636)
- [OAuth Scopes](../oauth-scopes.md)
- [API Reference](../api/index.md)
