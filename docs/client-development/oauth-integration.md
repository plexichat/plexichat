# OAuth integration guide

> **Note:** Plexichat does not currently operate as an OAuth 2.0 authorization server. There are no `/oauth2/authorize` or `/oauth2/token` endpoints, so Plexichat cannot issue OAuth 2.0 access tokens to third-party apps, support "Add to server" bot authorization URLs, or accept authorization-code or refresh-token grants. Everything in the [roadmap](#roadmap-plexichat-as-an-oauth-20-authorization-server) section is a design sketch for a future release. What is available today is inbound identity-provider sign-in with Google, GitHub, Microsoft, or GitLab. See [Inbound provider sign-in](#inbound-provider-sign-in-implemented).

## Overview

OAuth integration with Plexichat means one of two things:

1.  **Inbound identity-provider sign-in (implemented).** Plexichat acts as an OAuth 2.0 client and lets users sign in with an external provider (Google, GitHub, Microsoft, or GitLab).
2.  **OAuth 2.0 authorization server (not implemented).** Plexichat would act as the authorization server and issue tokens to third-party apps. This does not exist yet.

## Inbound provider sign-in (implemented)

Users sign in through an external provider. Plexichat performs the code-to-token exchange internally, so your app never handles provider tokens.

```
GET  /api/v1/auth/oauth/{provider}/login    →  { url, auth_url, state }
POST /api/v1/auth/oauth/{provider}/callback →  LoginResponse
```

Replace `{provider}` with `google`, `github`, `microsoft`, or `gitlab`. For full request and response shapes, see the [authentication API](../api/authentication.md#sign-in-with-oauth).

Plexichat uses PKCE toward the provider when enabled. The `state` value is single-use and short-lived (10 minutes). Mark it used after you verify it on callback.

### Google setup

1.  Create an OAuth 2.0 client in Google Cloud Console.
2.  Add the authorized redirect URI that matches your deployment's callback route (`/api/v1/auth/oauth/google/callback`).
3.  Add credentials to `config/config.yaml`:

    ```yaml
    oauth:
      google:
        client_id: "your_google_client_id"
        client_secret: "your_google_client_secret"
    ```

### GitHub setup

1.  Create an OAuth app in GitHub Developer Settings.
2.  Configure the callback URL for your deployment.
3.  Add credentials under `oauth.github` using the same keys as the Google example.

### Microsoft setup

1.  Register an app in Microsoft Entra ID (Azure portal).
2.  Configure the redirect URI for your deployment.
3.  Add credentials under `oauth.microsoft`.

The exact redirect URI depends on how your instance exposes `/api/v1/auth/oauth/{provider}/callback`. Configure the same URI at the provider.

## Roadmap: Plexichat as an OAuth 2.0 authorization server

The following capabilities have no implementation and no routes today. Do not target them; requests fail because the routes do not exist.

- `GET /oauth2/authorize` authorization endpoint with a consent screen
- `POST /oauth2/token` token endpoint (authorization-code grant, refresh grant)
- PKCE (S256) for public clients
- Bot or "add to server" authorization flows (`scope=bot&permissions=...`)
- Enforcement of scopes such as `identify`, `email`, `guilds`, `guilds.join`, and `bot`

Until this work ships, any guide that shows live examples against these endpoints is out of date.

## Custom inbound provider

To add another inbound provider for sign-in, follow the pattern of the built-in handlers in `src/api/routes/auth/oauth.py`:

1.  Implement a handler that builds the authorization URL, exchanges the code, and fetches user info.
2.  Register `GET /api/v1/auth/oauth/{provider}/login` and `POST /api/v1/auth/oauth/{provider}/callback` routes that reuse the existing state management.

This extends sign-in only. It does not make Plexichat an authorization server.

## Security considerations

These practices apply to the implemented inbound flow and to the future authorization-server design.

### State token

- Generate state with a cryptographically secure random source (32 bytes or more).
- Store state server-side with a short TTL (10 minutes).
- Verify state on callback and mark it used after a single use.

### PKCE

- Use SHA-256 for the code challenge (`S256`).
- Generate code verifiers with at least 32 bytes of entropy.
- Never include the code verifier in the authorization URL.

### Client secrets

- Never expose secrets in client-side code.
- Store secrets in environment variables or your system keyring.
- Rotate secrets periodically.

### Redirect URI validation

- Register every valid redirect URI and validate it strictly.
- Use HTTPS for redirect URIs in production.
- Prevent open redirects.

### Token storage

- Store session tokens securely. Use `HttpOnly` cookies where possible.
- Handle expiration and re-authentication.

## Related docs

- [Authentication API](../api/authentication.md)
- [API reference](../api/index.md)
- [OAuth 2.0 RFC 6749](https://tools.ietf.org/html/rfc6749)
- [PKCE RFC 7636](https://tools.ietf.org/html/rfc7636)
