# OAuth 2.0

This document describes the OAuth 2.0 implementation for third-party applications in Plexichat. Use it to authorize applications, validate scopes, and manage access and refresh tokens.

## Overview

The `oauth` package implements the authorization-code grant and token refresh. Tokens are formatted as `<type>.<id>.<secret>` where `secret` is a random URL-safe string hashed with SHA-256 and verified with constant-time comparison.

| File | Responsibility |
| --- | --- |
| `scopes.py` | Defines valid scopes and validates scope lists. |
| `tokens.py` | Generates and hashes authorization codes, access tokens, refresh tokens, and client secrets. Parses token strings. |
| `flows.py` | Implements `OAuth2Flow`, which validates requests, creates codes, and exchanges or refreshes tokens. |

## Scopes

### Valid scopes

`scopes.py:8` defines the following scopes:

*   `identify` — Access username, avatar, and banner.
*   `email` — Access email address.
*   `guilds` — List servers the user is in.
*   `guilds.join` — Join servers on behalf of the user.
*   `guilds.members.read` — Read member information in servers. Privileged scope.
*   `bot` — Add a bot to a server. Required for bot installs.
*   `applications.commands` — Create commands in servers the user manages.
*   `applications.commands.update` — Update application commands.
*   `messages.read` — Read messages. Privileged scope.
*   `webhook.incoming` — Create webhooks in channels the user manages.

Privileged scopes are `guilds.members.read` and `messages.read`. `BOT_REQUIRED_SCOPES` contains `bot`.

### Scope helpers

| Function | Description |
| --- | --- |
| `validate_scopes(scopes)` | Returns `(valid, issues)`. Fails if the list is empty or contains unknown scopes. |
| `parse_scopes(scope_string)` | Parses a space-separated scope string into a list. |
| `scopes_to_string(scopes)` | Converts a list to a sorted, space-separated string. |
| `has_scope(granted_scopes, required_scope)` | Returns `True` if the required scope is granted. |
| `get_scope_description(scope)` | Returns a human-readable description. |

```python
from src.core.applications.oauth.scopes import validate_scopes, parse_scopes

valid, issues = validate_scopes(["identify", "bot"])
scopes = parse_scopes("identify bot guilds")
```

## Tokens

`tokens.py` provides generation and verification helpers. Each generator returns a tuple of `(full_token, hash)`:

| Function | Format | Default random length |
| --- | --- | --- |
| `generate_authorization_code(code_id)` | `auth.<id>.<secret>` | 32 |
| `generate_access_token(token_id)` | `access.<id>.<secret>` | 32 |
| `generate_refresh_token(token_id)` | `refresh.<id>.<secret>` | 48 |
| `generate_interaction_token(interaction_id)` | `int.<id>.<secret>` | 32 |
| `generate_client_secret()` | `<secret>` | 32 |

Other helpers:

*   `hash_token(token)` — Hashes a token with SHA-256 and returns a hex digest.
*   `verify_token_hash(token, expected_hash)` — Verifies a token with `secrets.compare_digest`.
*   `parse_oauth_token(token)` — Parses a token string into `{"token_type", "id", "secret"}` or returns `None` for invalid format. Valid types are `auth`, `access`, `refresh`, and `int`.

## Flows

`OAuth2Flow` operates on the database tables `app_applications`, `app_oauth_codes`, and `app_oauth_tokens`.

### Configure the flow

```python
from src.core.applications.oauth import OAuth2Flow

config = {
    "token_expiry_seconds": 604800,
    "code_expiry_seconds": 600,
    "refresh_enabled": True,
    "authorization_endpoint": "/oauth2/authorize",
}
flow = OAuth2Flow(db, config)
```

### Generate and validate an authorization request

```python
url = flow.generate_authorization_url(
    application_id=123,
    redirect_uri="https://example.com/callback",
    scopes=["identify", "bot"],
    state="random-state",
)

info = flow.validate_authorization_request(
    application_id=123,
    redirect_uri="https://example.com/callback",
    scopes=["identify", "bot"],
)
```

`validate_authorization_request` verifies that the application exists, the redirect URI is registered, and all scopes are valid. It raises `InvalidClientError`, `InvalidRedirectUriError`, or `InvalidScopeError` on failure.

### Create and exchange an authorization code

```python
code_obj = flow.create_authorization_code(
    application_id=123,
    user_id=456,
    redirect_uri="https://example.com/callback",
    scopes=["identify"],
)
# code_obj.code contains the full code string to return to the client

tokens = flow.exchange_code(
    application_id=123,
    client_secret="client-secret",
    code=code_obj.code,
    redirect_uri="https://example.com/callback",
)
# tokens.access_token, tokens.refresh_token
```

`exchange_code` verifies the client secret, parses the code as type `auth`, checks that the code belongs to the application, is unused, is not expired, and that the hash and redirect URI match. It atomically marks the code as used and fails closed if `rowcount` is unavailable.

### Refresh and verify tokens

```python
new_tokens = flow.refresh_token(
    application_id=123,
    client_secret="client-secret",
    refresh_token=tokens.refresh_token,
)

info = flow.verify_access_token(new_tokens.access_token)
# {"token_id", "application_id", "user_id", "scopes", "expires_at"}
```

`refresh_token` requires `refresh_enabled` to be `True`. It verifies the client secret and the `refresh` token hash, revokes the old token, and issues a new pair. `verify_access_token` verifies the `access` hash with constant-time comparison before checking expiry, updates `last_used_at`, and raises `TokenExpiredError` or `TokenRevokedError` when appropriate.

### Revoke tokens

```python
revoked = flow.revoke_token(access_token, user_id=456)
count = flow.revoke_user_tokens(application_id=123, user_id=456)
```

`revoke_token` verifies the presented secret against the stored hash and, if `user_id` is provided, verifies ownership. It returns `False` for unknown IDs or secret mismatches. `revoke_user_tokens` revokes all non-revoked tokens for a user on an application.

## Error handling

Flows raise typed exceptions from `src.core.applications.exceptions`:

*   `InvalidClientError` — Unknown application or invalid client secret.
*   `InvalidGrantError` — Invalid, reused, or mismatched code or token.
*   `InvalidScopeError` — Unknown or empty scope list.
*   `InvalidRedirectUriError` — Redirect URI not registered.
*   `AuthorizationCodeExpiredError` — Code has expired.
*   `TokenExpiredError` — Access token has expired.
*   `TokenRevokedError` — Token has been revoked.

## Related code

*   `plexichat/src/core/applications/oauth/scopes.py:42` — `validate_scopes`
*   `plexichat/src/core/applications/oauth/tokens.py:10` — `generate_authorization_code`
*   `plexichat/src/core/applications/oauth/tokens.py:95` — `hash_token`
*   `plexichat/src/core/applications/oauth/flows.py:33` — `OAuth2Flow`
*   `plexichat/src/core/applications/oauth/flows.py:194` — `exchange_code`
*   `plexichat/src/core/applications/oauth/flows.py:362` — `verify_access_token`
