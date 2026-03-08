# Security Guidance

This page documents public security expectations and integration behavior. It does not publish secret values, private infrastructure layout, or internal operator runbooks.

## Authentication Model

- user requests typically use `Authorization: Bearer <token>`
- bot requests use `Authorization: Bot <token>`
- some servers may also require `X-API-Access-Token` on authenticated API traffic
- the capability endpoint reports whether the access-token gate is active

## Session and Identity Hygiene

- negotiate client compatibility before startup-sensitive operations
- treat session and bot tokens as bearer credentials
- revoke and rotate credentials when exposure is suspected
- prefer least-privilege bot scopes and avoid sharing tokens across systems

## Two-Factor Authentication

The authentication routes support TOTP-based 2FA flows and recovery behavior. Clients should be prepared for login responses that require a second step rather than immediately returning a session token.

## Transport Expectations

- REST and WebSocket clients should use the deployment's intended secure transport
- clients should reconnect with backoff instead of retry loops when authentication or gateway failures occur
- signed or temporary media URLs should be treated as time-bounded credentials

## Rate-Limit and Abuse Protections

The backend applies global, user, IP, and route-level rate limiting. Clients should:

- respect `429` responses
- use exponential backoff
- avoid concurrent retry storms
- cache or batch where practical

## Sensitive Information Handling

Keep these items out of repository docs and client bundles:

- encryption keys and secret material
- database credentials and connection strings
- internal-only admin procedures
- environment-specific firewall, proxy, or host details

## Related Pages

- [Access Tokens](admin-access-tokens.md)
- [Rate Limits](rate-limits.md)
- [Security Logout](security-logout.md)

