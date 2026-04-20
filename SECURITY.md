# Security Policy - Plexichat Server

**Last updated:** 2025-01

## Reporting Vulnerabilities

If you discover a security vulnerability in Plexichat, please report it privately via:
- **Email:** security@plexichat.com
- **GitLab:** Create a confidential issue on the project tracker

**Do not** file public issues for security vulnerabilities.

## Architecture Overview

Plexichat is a self-hosted, sovereignty-first real-time messaging platform built with Python/FastAPI. It provides REST API endpoints, WebSocket gateway for real-time communication, and supports SQLite or PostgreSQL backends.

### Trust Boundaries

| Boundary | Description |
|----------|-------------|
| **Public (unauthenticated)** | Login, register, password reset, OAuth endpoints |
| **Authenticated (JWT/session)** | Messaging, servers, channels, relationships, presence |
| **Server admin** | Server management, member management, channel creation |
| **Platform admin** | System config, user management, global moderation |
| **Internal (self-test)** | Health checks, self-test endpoints (restricted to localhost) |

## Security Controls

### Authentication
- **Password hashing:** Configurable (bcrypt/argon2) with salted hashes
- **Session tokens:** Cryptographically random, hashed before storage
- **JWT tokens:** HMAC-signed with configurable expiry and rotation
- **OAuth2:** PKCE-protected authorization code flow with state verification

### Authorization
- **Role hierarchy:** Platform admin > server owner > server admin > moderator > member
- **Permission system:** Fine-grained bitfield permissions per role
- **Route middleware:** JWT validation + role/permission checks on protected endpoints
- **Server scoping:** Operations scoped to the server the user belongs to

### Anti-Abuse
- **Rate limiting:** Multi-tier (global, auth, per-route, per-user) with database or Redis backend
- **Auto-moderation:** Configurable keyword/regex/spam/AI rules with violation tracking
- **Spam detection:** Message frequency, duplicate detection, caps/emoji limits
- **CORS:** Configurable allowed origins, strict by default

### Encryption
- **At rest:** AES-256-GCM for sensitive fields (user notes, configuration secrets)
- **Key management:** TPM 2.0 (hardware-bound) or environment variable (software-bound)
- **Fail-closed:** Encryption failures raise errors rather than storing plaintext
- **Media keys:** Separate PLEXICHAT_MEDIA_KEY for media encryption (32-byte Base64)

### SSRF Protection
- **URL validation:** Scheme allowlisting (http/https only), private IP blocking
- **DNS rebinding:** Short TTL on DNS resolution cache (30s) to reduce TOCTOU window
- **Redirect following:** Validated on each hop to prevent redirect-based SSRF
- **IP blacklisting:** Loopback, link-local, private ranges blocked on outbound requests

### Admin Security
- **Admin UI:** Disabled by default; must be explicitly enabled in config
- **Self-test bypass:** Internal secret only accepted from localhost/trusted proxy IPs
- **Error sanitization:** Template errors do not leak internal details to users
- **XSS prevention:** HTML content sanitized before rendering; script tags stripped

## Known Configuration Requirements

The following **must** be configured before production deployment:

| Setting | Description | Default |
|---------|-------------|---------|
| PLEXICHAT_SYSTEM_KEY | 64-char hex key for encryption at rest | Insecure local file fallback |
| PLEXICHAT_MEDIA_KEY | 32-byte Base64 key for media encryption | Not set (media module fails) |
| PLEXICHAT_MESSAGE_KEY | 32-byte Base64 key for message encryption | Not set |
| Redis password | Authentication for Redis backend | Empty (auth fails) |
| admin_ui.enabled | Enable admin web interface | false |
| require_secure_source | Require TPM or env key source | true |
