# Feature Overview

This page provides a comprehensive overview of Plexichat's feature areas. Each section links to detailed documentation for configuration and API usage.

## Core Platform

### Authentication and Identity

Plexichat provides a complete authentication system with multiple layers:

- **Session-based authentication**: Bearer tokens for users, dedicated tokens for bots
- **TOTP two-factor authentication**: Configurable 2FA with backup codes, compatible with standard authenticator apps
- **Password policy**: Configurable minimum length (12 default), complexity requirements, and Argon2id hashing
- **Account lockout**: Configurable failed-attempt threshold and lockout duration
- **Account deletion**: GDPR-compliant deletion with grace period, content anonymization, and audit logging
- **OAuth login**: Google, GitHub, and Microsoft OAuth with PKCE protection

Configuration: [Authentication Configuration](config-authentication.md) | API: [Authentication Routes](api/authentication.md)

### Users and Profiles

- User profiles with avatars, display names, bios, and banner images
- User notes (personal annotations on other users)
- User settings (theme, notifications, privacy)
- Feature flags and tier-based capabilities (standard, alpha, premium, staff)
- Badges and achievements

API: [Users Routes](api/users.md) | [Avatars Routes](api/avatars.md) | [Features Routes](api/features.md)

### Relationships

- Friend requests with accept/decline flows
- Block list for preventing unwanted contact
- Mutual relationship queries (is friend, is blocked)

API: [Relationships Routes](api/relationships.md)

### Servers and Channels

- Guild-style organization with roles, permissions, and channel hierarchy
- Text and voice channels with per-channel permission overrides
- Server invites with configurable codes and limits
- Server templates for quick setup (configurable channels, roles, onboarding steps)
- Events system for scheduled and recurring events
- Server discovery with tags, bumping, and member thresholds

Configuration: See `servers` section in [Default Configuration Reference](default-config.md) | API: [Servers Routes](api/servers.md) | [Channels Routes](api/channels.md)

### Messaging

- Message creation, editing, deletion with read state tracking
- Message replies (threaded conversations)
- Message pins (per-channel)
- Reactions with emoji and custom emoji support
- Message search (see Search below)
- Attachment handling with resumable uploads (see Media below)
- Message encryption at rest (configurable)
- Per-user message length settings (tier-based)

Configuration: See `messaging` section in [Default Configuration Reference](default-config.md) | API: [Messages Routes](api/messages.md) | [Reactions Routes](api/reactions.md)

---

## Real-Time Communication

### WebSocket Gateway

The gateway provides persistent, real-time communication using a heartbeat + dispatch model:

- **Connection flow**: Connect → HELLO → IDENTIFY → Heartbeat → Events
- **Resume support**: Reconnect and resume missed events without full re-sync
- **Dispatch events**: MESSAGE_CREATE, PRESENCE_UPDATE, TYPING_START, and more
- **Gateway intents**: Subscribe to specific event categories (configurable, some require privileges)
- **Compression**: Per-message deflate (RFC 7692) for bandwidth savings

Configuration: [WebSocket Configuration](config-websocket.md) | Docs: [Gateway Overview](websocket/index.md) | [Events](websocket/events.md) | [Intents](websocket/intents.md)

### Presence and Typing

- Online/idle/DND/invisible status with automatic idle detection
- Typing indicators with configurable timeout (10 seconds default)
- Presence updates pushed via gateway events

API: [Presence Routes](api/presence.md)

### Voice

- WebRTC-based voice channels with ICE server discovery
- SFU (Selective Forwarding Unit) backend: mediasoup (default) or Janus
- STUN/TURN server configuration for NAT traversal
- Voice connection metadata and signaling via gateway

Configuration: [Voice Configuration](config-voice.md) | API: [Voice Routes](api/voice.md)

### Notifications

- Unread notification feed with per-notification read state
- Read-all and per-notification read operations
- Pushed via gateway events for real-time updates

API: [Notifications Routes](api/notifications.md)

---

## Content and Safety

### Media

- File uploads with resumable upload sessions for large files
- Multiple storage backends: local filesystem, S3-compatible, database blob
- Image optimization and thumbnail generation (configurable sizes: 64, 128, 256, 512)
- Perceptual hashing (phash) for duplicate and similar-image detection
- Content deduplication with automatic blocking of known-hash files
- Virus scanning via ClamAV (optional, requires dedicated scanner)
- Media URL signing with time-limited, tamper-proof URLs
- Auto-routing of small text-based files to database storage
- Type-specific size limits (image: 10MB, video: 100MB, audio: 50MB, document: 25MB)
- At-rest encryption for stored media

Configuration: [Media Configuration](config-media.md) | API: [Media Routes](api/media.md)

### Search and Discovery

- Full-text search powered by SQLite FTS5 (default backend)
- Message, user, and server search
- Server discovery directory with tags, bumping, and member thresholds
- Write-time indexing for immediate searchability of new content

Configuration: [Search Configuration](config-search.md) | API: [Search Routes](api/search.md)

### Reports and Feedback

- User-submitted reports for content and behavior violations
- Feedback submission with rate-limited endpoints
- Auto-moderation (automod) with configurable rules and AI-backed detection

API: [Reports Routes](api/reports.md) | [Feedback Routes](api/feedback.md)

### Rate Limiting

- Multi-tier rate limiting: global, per-user, per-IP, per-route
- Bot and webhook multipliers for appropriate scaling
- Admin and internal bypass options
- Configurable bypass secret for service-to-service communication

Configuration: [Rate Limiting Configuration](config-rate-limiting.md) | Docs: [Rate Limits](rate-limits.md)

---

## Extensibility

### Applications and Bots

- Bot accounts with dedicated authentication tokens
- Application system for OAuth2 integrations
- Slash commands and interaction handling
- Webhook execution for inbound integrations
- Per-application rate limits

Configuration: See `applications` section in [Default Configuration Reference](default-config.md) | API: [Webhooks Routes](api/webhooks.md)

### Webhooks

- Inbound webhook execution (post messages from external services)
- Webhook management (create, update, delete)
- Signature verification for webhook authenticity

Configuration: See `webhooks` section in [Default Configuration Reference](default-config.md)

### Polls

- Message-attached polls with configurable option count and duration
- Vote tracking and result aggregation
- Duration limits: 1 hour minimum, 168 hours (7 days) maximum

Configuration: See `polls` section in [Default Configuration Reference](default-config.md) | API: [Polls Routes](api/polls.md)

### Emojis, Stickers, and Soundboard

- Custom emoji support (up to 50 per server, 256KB max size)
- Animated emoji support
- Sticker packs with per-server and per-pack limits
- Soundboard with configurable cooldowns and size limits

Configuration: See `emojis`, `stickers`, `soundboard` sections in [Default Configuration Reference](default-config.md) | API: [Emojis Routes](api/emojis.md)

---

## Operations and Administration

### Admin UI

- Web-based admin dashboard
- User management, server oversight, and configuration editing
- Protected by OTP requirement and host restriction by default
- Separate rate limiting from API endpoints

Configuration: See `admin_ui` section in [Default Configuration Reference](default-config.md) | API: [Admin Routes](api/admin.md)

### Monitoring and Telemetry

- Built-in telemetry for endpoint response times
- Configurable alert thresholds for CPU, memory, database, and API performance
- Slow query detection and alerting
- Health and status endpoints for external monitoring

Configuration: See `monitoring` section in [Default Configuration Reference](default-config.md) | API: [Telemetry Routes](api/telemetry.md) | [System Routes](api/system.md)

### Self-Test Infrastructure

- Built-in self-test suite for validating API behavior
- Configurable test scope (excluded endpoints, stack trace capture)
- Must never be enabled in production (creates test user with known credentials)

Configuration: See `selftest` section in [Default Configuration Reference](default-config.md)

---

## Related Documentation

- [Getting Started](getting-started.md) — First steps with the API
- [Configuration Overview](configuration.md) — How to configure your server
- [Security Best Practices](security.md) — Production security guidance
- [Performance Tuning](performance.md) — Scaling and optimization
- [API Reference](api/index.md) — Detailed route documentation
