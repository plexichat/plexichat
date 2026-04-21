# API Access Tokens

Some Plexichat deployments require an additional access token for authenticated REST API requests. This page explains how the access token gate works, when to use it, and how to configure it.

## When Access Tokens Apply

When access-token gating is enabled, every authenticated API request requires **two** credentials:

1. **Session or bot authorization**: `Authorization: Bearer <session-token>` or `Authorization: Bot <bot-token>`
2. **Access token**: `X-API-Access-Token: <access-token>`

This provides defense-in-depth: even if a session token is leaked, the attacker cannot make API requests without the separate access token.

## Detecting the Requirement

Clients can discover whether access-token gating is active without making authenticated requests:

```bash
curl {{BASE_URL}}/capabilities
```

The response includes an `access_token_required` field. If `true`, the `X-API-Access-Token` header must be included on all authenticated requests.

**Client Implementation**

- Check `GET /capabilities` on startup and when configuration changes
- If `access_token_required: true`, prompt the user for the access token or read it from configuration
- If `access_token_required: false`, omit the header — it is not required and will be ignored
- Cache the capability check result, but re-check periodically (e.g., on reconnect or hourly)

## Request Shape

With access-token gating enabled:

```http
GET /api/v1/users/@me HTTP/1.1
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
X-API-Access-Token: plexi_at_a1b2c3d4e5f6...
```

Without access-token gating:

```http
GET /api/v1/users/@me HTTP/1.1
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

## Why Use Access Token Gating

This mechanism is useful for:

- **Closed deployments**: Private instances where only authorized users should access the API, even if they somehow obtain a valid session token
- **Staged testing**: Pre-release environments where you want to limit API access to testers while the frontend is publicly accessible
- **Regulatory compliance**: Environments that require multiple authentication factors for API access
- **Temporary lockdown**: Quickly restrict API access during a security incident without revoking all sessions

## Security Practices

**Treat access tokens as sensitive credentials**

- Never hardcode access tokens into public client applications (web, mobile, desktop)
- Store access tokens in secure configuration (environment variables, secure storage)
- Support rotation — access tokens should be changeable without requiring client code changes
- Support revocation — if an access token is compromised, it should be revocable immediately

**Surface clear errors**

- When a server requires an access token but none is provided, return a clear error that distinguishes this from authentication failure
- The error should indicate that an access token is needed, not reveal the token value or format
- Client applications should present a user-friendly message (e.g., "This server requires an API access token. Please configure it in settings.")

**Access token vs session token**

- `Scope` (Single user session): Entire API (if valid)
- `Lifetime` (Hours to days (configurable)): Until rotated by admin
- `Source` (Generated on login): Admin-configured
- `Header` (`Authorization: Bearer`): `X-API-Access-Token`
- `Per-user` (Yes (one per session)): No (shared across users)

Because the access token is shared across all users of the deployment, it has a higher blast radius if compromised. Protect it accordingly.

## Configuration

Access token gating is discovered at runtime via the capabilities endpoint. The token itself is configured at the server level by the administrator.

**Enabling access token gating** involves setting the access token value in the server's runtime configuration. The exact configuration depends on your deployment method.

## Error Handling

When the access token gate is active and a request is missing or has an invalid access token:

- **Status**: `403 Forbidden` (not 401 — the user is authenticated, but access is denied)
- **Error code**: Specific to access-token failure, distinguishable from permission errors
- **Message**: Indicates that an access token is required

Clients should handle this differently from a 401 (authentication failure) — a 401 means the session is invalid and the user needs to re-login, while a 403 from the access token gate means the access token needs to be configured.

## Related Documentation

- [Security Best Practices](security.md) — Overall security model and practices
- [Authentication Configuration](config-authentication.md) — Session and token configuration
- [Errors](errors.md) — Error response formats and codes
- [Access Blocked](access-blocked.md) — Access denied scenarios and resolution
