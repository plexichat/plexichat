# OAuth Integration Guide

> **NOT IMPLEMENTED — ROADMAP:** Plexichat does **not** currently act as an
> OAuth2 *authorization server*. There are no `/oauth2/authorize` or
> `/oauth2/token` endpoints, so Plexichat cannot issue OAuth2 access tokens to
> third-party applications, support "Add to server" bot authorization URLs, or
> accept authorization-code/refresh-token grants. Everything in the
> [Roadmap](#roadmap-plexichat-as-an-oauth2-authorization-server) section below
> is a design sketch for a future release — none of it can be executed today.
>
> What **is** available today is *inbound* identity-provider login: users can
> sign in to Plexichat using Google, GitHub, Microsoft, or GitLab accounts.
> See [Inbound Provider Login (Implemented)](#inbound-provider-login-implemented).

## Overview

Two distinct things are often called "OAuth integration":

1. **Inbound IdP login (implemented):** Plexichat consumes external OAuth2
   providers (Google, GitHub, Microsoft, GitLab) so users can authenticate
   with those accounts. Plexichat acts as an OAuth2 *client* only.
2. **OAuth2 authorization server (not implemented):** Plexichat would expose
   `/oauth2/authorize` and `/oauth2/token` so third-party apps and bots could
   obtain user-scoped tokens. This does not exist yet.

## Inbound Provider Login (Implemented)

Users sign in through an external identity provider:

```
GET  /api/v1/auth/oauth/{provider}/login     → returns {url, state, code_verifier}
POST /api/v1/auth/oauth/{provider}/callback   → completes login, returns LoginResponse
```

Supported providers: `google`, `github`, `microsoft`, `gitlab`. Full request/response
details are documented in the [Authentication API](../api/authentication.md#oauth-sign-in).

Plexichat performs the code-to-token exchange internally on the callback; your
application never handles provider tokens. PKCE is used toward the provider when
enabled, and state values are single-use and short-TTL.

### Google Setup

1. Create an OAuth 2.0 client in Google Cloud Console.
2. Add the authorized redirect URI used by your deployment's callback route.
3. Configure credentials under the `oauth.google` section of `config/config.yaml`:

```yaml
oauth:
  google:
    client_id: "your_google_client_id"
    client_secret: "your_google_client_secret"
```

### GitHub Setup

1. Create an OAuth App in GitHub Developer Settings.
2. Configure the callback URL for your deployment.
3. Configure credentials under `oauth.github`.

### Microsoft Setup

1. Register an app in Azure Portal (Entra ID).
2. Configure the redirect URI for your deployment.
3. Configure credentials under `oauth.microsoft`.

The exact redirect/callback URLs depend on how your instance exposes
`/api/v1/auth/oauth/{provider}/callback`; configure the matching URI at the
provider. See [Authentication API](../api/authentication.md#oauth-sign-in) for
the callback contract.

## Roadmap: Plexichat as an OAuth2 Authorization Server

The following capabilities have been discussed for a future release but have
**no implementation and no routes today**:

- `GET /oauth2/authorize` authorization endpoint with consent screen
- `POST /oauth2/token` token endpoint (authorization-code grant, refresh grant)
- PKCE (S256) support for public clients
- Bot/add-to-server authorization flows (`scope=bot&permissions=...`)
- Enforcement of application scopes such as `identify`, `email`, `guilds`,
  `guilds.join`, `bot`

Until that work ships, no client should target these endpoints — requests will
fail (the routes do not exist), and any guide showing live examples against them
is out of date.

## Custom Inbound Provider

Server developers can add another *inbound* identity provider by following the
pattern of the built-in ones in `src/api/routes/auth/oauth.py`: implement a
provider handler (authorization URL, code exchange, user-info fetch) and register
`GET .../oauth/{provider}/login` plus `POST .../oauth/{provider}/callback` routes
that reuse the existing state management. This is for sign-in only; it does not
make Plexichat an authorization server.

## Security Considerations

These practices apply to the implemented inbound flow and to the future
authorization-server design:

### State Token Security

- Use cryptographically secure random values (32+ bytes)
- Store state server-side with a short TTL (10 minutes)
- Verify state on callback and mark it used after a single use

### PKCE Security

- Use SHA-256 for the code challenge (S256 method)
- Generate code verifiers with sufficient entropy (32+ bytes)
- Never include the code verifier in an authorization URL

### Client Secret Security

- Never expose secrets in client-side code
- Use environment variables or the system keyring for secrets
- Rotate secrets periodically

### Redirect URI Validation

- Register all valid redirect URIs and validate them strictly
- Use HTTPS for production redirect URIs
- Prevent open redirects

### Token Storage

- Store session tokens securely (HttpOnly cookies where possible)
- Implement expiration handling and re-authentication

## Resources

- [OAuth 2.0 RFC 6749](https://tools.ietf.org/html/rfc6749)
- [PKCE RFC 7636](https://tools.ietf.org/html/rfc7636)
- [Authentication API](../api/authentication.md)
- [API Reference](../api/index.md)
