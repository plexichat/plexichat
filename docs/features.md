# Feature overview

This page summarizes Plexichat's feature areas. Each section links to configuration and API reference.

## Core platform

### Authentication and identity

Plexichat provides multiple authentication layers:

- **Session authentication** — Bearer tokens for users and dedicated tokens for bots.
- **TOTP two-factor authentication** — Configurable 2FA with backup codes, compatible with standard authenticator apps.
- **Password policy** — Configurable minimum length (12 by default), complexity requirements, and Argon2id hashing.
- **Account lockout** — Configurable failed-attempt threshold and lockout duration.
- **Account deletion** — GDPR-compliant deletion with grace period, content anonymization, and audit logging.
- **OAuth login** — Google, GitHub, and Microsoft OAuth with PKCE protection.

Configuration: [Authentication configuration](deployment/configuration/config-authentication.md) · API: [Authentication routes](api/authentication.md)

### Users and profiles

- User profiles with avatars, display names, bios, and banner images.
- User notes (personal annotations on other users).
- User settings (theme, notifications, privacy).
- Feature flags and tier-based capabilities (`standard`, `alpha`, `premium`, `staff`).
- Badges and achievements.

API: [Users routes](api/users.md) · [Avatars routes](api/avatars.md) · [Features routes](api/features.md)

### Relationships

- Friend requests with accept and decline flows.
- Block list that prevents unwanted contact.
- Mutual relationship queries (is friend, is blocked).

API: [Relationships routes](api/relationships.md)

### Servers and channels

- Guild-style organization with roles, permissions, and channel hierarchy.
- Text and voice channels with per-channel permission overrides.
- Server invites with configurable codes and limits.
- Server templates for quick setup (configurable channels, roles, and onboarding steps).
- Events system for scheduled and recurring events.
- Server discovery with tags, bumping, and member thresholds.

Configuration: `servers` section in [Default configuration reference](default-config.md) · API: [Servers routes](api/servers.md) · [Channels routes](api/channels.md)

### Messaging

- Message creation, editing, and deletion with read-state tracking.
- Message replies (threaded conversations).
- Message pins (per channel).
- Reactions with emoji and custom emoji support.
- Message search (see Search below).
- Attachment handling with resumable uploads (see Media below).
- Message encryption at rest (configurable).
- Per-user message length settings (tier-based).

Configuration: `messaging` section in [Default configuration reference](default-config.md) · API: [Messages routes](api/messages.md) · [Reactions routes](api/reactions.md)

### Message tools

- **Bookmarks** — Save messages for later; list per user with an optional conversation filter.
- **Message forwarding** — Copy a message into another conversation where you can post.
- **Scheduled messages** — Queue a message for future delivery; list and cancel pending sends.

API: [Features routes](api/features.md)

## Real-time communication

### WebSocket gateway

The gateway provides persistent, real-time communication with a heartbeat and dispatch model:

- **Connection flow** — Connect, then `HELLO`, then `IDENTIFY`, then heartbeat and events.
- **Resume support** — Reconnect and resume missed events without a full re-sync.
- **Dispatch events** — `MESSAGE_CREATE`, `PRESENCE_UPDATE`, `TYPING_START`, and others. See [Gateway events](websocket/events.md) for the full list.
- **Gateway intents** — Subscribe to specific event categories. See [Gateway intents](websocket/intents.md). Default intents include `ARTIFACTS` (`1<<20`) and `SUMS` (`1<<21`); privileged intents are `GUILD_MEMBERS` (`1<<1`), `GUILD_PRESENCES` (`1<<8`), and `MESSAGE_CONTENT` (`1<<15`) (`src/core/events/types.py:97`).
- **Compression** — Per-message deflate (RFC 7692).

Configuration: [WebSocket configuration](deployment/configuration/config-websocket.md) · Docs: [Gateway overview](websocket/index.md) · [Events](websocket/events.md) · [Intents](websocket/intents.md)

### Presence and typing

- Online, idle, DND, and invisible status with automatic idle detection.
- Typing indicators with a configurable timeout (10 seconds by default).
- Presence updates pushed through gateway events.

API: [Presence routes](api/presence.md)

### Voice

- WebRTC-based voice channels with ICE server discovery and TURN credential generation (HMAC-SHA1, RFC 5389).
- Signaling is relayed through the gateway. Media is peer-to-peer mesh by default; you can enable an SFU backend for larger channels.
- Supported SFU backends (`src/core/voice/signaling/sfu/__init__.py:45`):
  - `aiortc` — In-process Python SFU. This is the default (`voice.sfu_backend` in `src/config_defaults.py:396`).
  - `mediasoup` — REST adapter for a custom mediasoup server.
  - `mediasoup-ws` — WebSocket adapter for mediasoup-demo.
  - `janus` — REST adapter for Janus Gateway.
- STUN and TURN server configuration for NAT traversal.
- Voice-channel lifecycle tied to channel occupancy (call starts when the first participant joins, ends when the last leaves).

Configuration: [Voice configuration](deployment/configuration/config-voice.md) · API: [Voice routes](api/voice.md) · Module docs: [Voice module](../src/core/voice/README.md)

### Notifications

- Unread notification feed with per-notification read state.
- Read-all and per-notification read operations.
- Pushed through gateway events for real-time updates.

API: [Notifications routes](api/notifications.md)

## Content and safety

### Media

- File uploads with resumable upload sessions for large files.
- Multiple storage backends: local filesystem, S3-compatible, or database blob.
- Image optimization and thumbnail generation (configurable sizes: 64, 128, 256, 512).
- Perceptual hashing (phash) for duplicate and similar-image detection.
- Content deduplication with automatic blocking of known-hash files.
- Virus scanning through ClamAV (optional, requires a dedicated scanner).
- Media URL signing with time-limited, tamper-proof URLs.
- Auto-routing of small text-based files to database storage.
- Type-specific size limits (image: 10 MB, video: 100 MB, audio: 50 MB, document: 25 MB).
- At-rest encryption for stored media.

Configuration: [Media configuration](deployment/configuration/config-media.md) · API: [Media routes](api/media.md)

### Search and discovery

- Full-text search powered by SQLite FTS5 (default backend).
- Message, user, and server search.
- Server discovery directory with tags, bumping, and member thresholds.
- Write-time indexing for immediate searchability of new content.

Configuration: [Search configuration](deployment/configuration/config-search.md) · API: [Search routes](api/search.md)

### Reports and feedback

- User-submitted reports for content and behavior violations.
- Feedback submission with rate-limited endpoints.
- Auto-moderation (automod) with configurable rules and AI-backed detection.

API: [Reports routes](api/reports.md) · [Feedback routes](api/feedback.md)

### Rate limiting

- Multi-tier rate limiting: global, per-user, per-IP, per-route.
- Bot and webhook multipliers for appropriate scaling.
- Admin and internal bypass options.
- Internal services skip rate limits through the runtime-generated internal secret header.

Configuration: [Rate limiting configuration](deployment/configuration/config-rate-limiting.md) · Docs: [Rate limits](rate-limits.md)

## Extensibility

### Applications and bots

- Bot accounts with dedicated authentication tokens.
- Application system (inbound OAuth2 provider login; acting as an OAuth2 authorization server is not yet implemented).
- Interaction handling through the applications callback route (slash-command registration is not yet implemented).
- Webhook execution for inbound integrations.
- Per-application rate limits.

Configuration: `applications` section in [Default configuration reference](default-config.md) · API: [Webhooks routes](api/webhooks.md)

### Webhooks

- Inbound webhook execution (post messages from external services).
- Webhook management (create, update, delete).
- Signature verification for webhook authenticity.

Configuration: `webhooks` section in [Default configuration reference](default-config.md)

### Polls

- Message-attached polls with configurable option count and duration.
- Vote tracking and result aggregation.
- Duration limits: 1 hour minimum, 168 hours (7 days) maximum.

Configuration: `polls` section in [Default configuration reference](default-config.md) · API: [Polls routes](api/polls.md)

### Emojis, stickers, and soundboard

- Custom emoji support (up to 50 per server, 256 KB max size).
- Animated emoji support.
- Sticker packs with per-server and per-pack limits.
- Soundboard with configurable cooldowns and size limits.

Configuration: `emojis`, `stickers`, `soundboard` sections in [Default configuration reference](default-config.md) · API: [Emojis routes](api/emojis.md), [Soundboard API](api/soundboard.md)

## Operations and administration

### Admin UI

- Web-based admin dashboard.
- User management, server oversight, and configuration editing.
- Protected by OTP requirement and host restriction by default.
- Separate rate limiting from API endpoints.

Configuration: `admin_ui` section in [Default configuration reference](default-config.md) · API: [Admin routes](api/admin.md)

### Monitoring and telemetry

- Built-in telemetry for endpoint response times.
- Configurable alert thresholds for CPU, memory, database, and API performance.
- Slow-query detection and alerting.
- Health and status endpoints for external monitoring.

Configuration: `monitoring` section in [Default configuration reference](default-config.md) · API: [Telemetry routes](api/telemetry.md) · [System routes](api/system.md)

### Self-test infrastructure

- Built-in self-test suite for validating API behavior.
- Configurable test scope (excluded endpoints, stack-trace capture).
- Safe for production (auto-generated random credentials, cleans up after itself).

Configuration: `selftest` section in [Default configuration reference](default-config.md)

## Related documentation

- [Getting started](getting-started.md) — First steps with the API.
- [Configuration overview](configuration.md) — How to configure your server.
- [Security best practices](security.md) — Production security guidance.
- [Performance tuning](performance.md) — Scaling and optimization.
- [API reference](api/index.md) — Detailed route documentation.
