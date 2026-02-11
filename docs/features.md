# PlexiChat Features

This document provides a comprehensive overview of all PlexiChat features, organized by component. Features marked with **[WIP]** are work in progress and not yet fully implemented.

## Server Features

### Authentication & Security

- User registration with email verification
- Login with username/password
- Two-factor authentication (TOTP) with backup codes
- Session management (view/revoke active sessions)
- Device tracking and management
- Account lockout after failed attempts
- Password strength requirements (12+ chars, uppercase, lowercase, digit, special character)
- Bot account creation with restricted permissions
- JWT-style session tokens with instant revocation
- Token caching for performance (Redis optional)
- IP-based token binding (optional security feature)

### Messaging

- Direct messages (one-on-one and group)
- Group conversations with roles (owner, admin, member)
- Message editing (own messages only)
- Message deletion (soft delete with audit trail)
- Message pinning
- Message replies
- Message attachments (images, videos, audio, documents)
- Rich text formatting (bold, italic, strikethrough, code, spoilers, quotes)
- Message reactions with Unicode and custom emoji
- Message search with advanced query syntax
- Delivery and read receipts
- Content filtering (profanity, NSFW, custom blocked words)
- Message preview length configuration
- Zero-friction at-rest encryption for messages
- Per-user configurable message limits

### Servers & Channels

- Server creation and management
- Server templates for cloning structure
- Welcome screens for new members
- Onboarding flows with customizable steps
- Text channels with topics
- Voice channels with user limits and bitrate
- Stage channels with speaker/audience management
- Channel categories for organization
- Channel permission overrides
- Role-based access control (RBAC)
- Member management (kick, ban, nickname, roles)
- Invite system with expiration and usage limits
- Audit logging for all server actions
- Scheduled events with RSVP tracking
- Recurring events with RRULE support

### Voice & Video

- Voice channel joining/leaving
- Self-mute/self-deaf controls
- Server moderation (server mute/deafen, move, disconnect)
- Stage channel speaker management
- Request to speak (raise hand) functionality
- Screen sharing support
- WebRTC signaling via Mediasoup or Janus
- TURN server integration for NAT traversal
- Voice region selection
- AFK channel with auto-move timeout

### Media & Content

- File upload with local/S3/MinIO storage
- Image processing (thumbnails, resizing, format conversion)
- Video metadata extraction (duration, resolution, codec)
- Secure URL signing with HMAC expiration
- External URL proxy with SSRF protection
- Content-type validation and magic byte verification
- Malware scanning interface (ClamAV)
- Per-user rate limiting for uploads
- Perceptual hashing for image deduplication
- Media file deduplication via SHA-256 checksums

### User Features & Badges

- User feature flags (admin-controlled)
- User tiers (standard, alpha, premium) with rate limit multipliers
- Profile badges (alpha_tester, staff, verified, etc.)
- Rate limit tier configuration
- Feature expiration support
- Usage tracking per tier

### Discovery & Community

- Public server directory
- Server categories (gaming, music, education, etc.)
- Server verification levels
- Server bumping with cooldown
- Server listing management
- User search by username
- Server search by name/description/tags

### Moderation & Safety

- Auto-moderation with configurable rules
- Rule types: keyword, regex, message spam, mention spam, invite links, external links, caps percentage, mass emoji, repeated characters
- Multiple action types: delete, timeout, kick, ban, alert moderators
- Exempt roles and channels
- User reputation scoring
- AI moderation backend support (OpenAI, Perspective API, custom)
- Bulk message scanning for raids
- Audit logging for automod actions

### Applications & Bots

- OAuth2 authorization flows
- Application registration and management
- Slash command registration (global and per-server)
- Command option types (string, integer, boolean, user, channel, role, mentionable, number, attachment)
- Autocomplete support for command options
- Interaction handling (slash commands, buttons, select menus, modals, context menus)
- Component builders with validation
- Webhook-based interaction endpoint
- Rate limiting per application
- Application installation tracking per server

### Admin Panel

- Admin user management
- Feedback/ticket viewing and management
- Internal notes on tickets
- Host restriction for admin access
- Telemetry dashboard
- Self-test validation suite

### Webhooks

- Webhook creation with secure token generation
- Webhook management (get, update, delete, regenerate token)
- Webhook execution to send messages
- Username and avatar override per message
- Rich embed support (up to 10 embeds per message)
- Thread posting support
- Token stored as hash for security

### Polls

- Poll creation attached to messages
- Poll options (2-10 choices)
- Poll duration (1 hour to 7 days or no expiry)
- Single or multiple choice voting
- Results visibility control (always, after vote, after end)
- Vote tracking per user
- Early poll closure by creator
- Automatic expiry handling

### Stickers

- Sticker packs (default, server custom, user purchased)
- Sticker upload with image validation (PNG/APNG/Lottie JSON)
- Sticker metadata (name, tags, related emoji)
- Sticker suggestions based on message content
- Sticker usage tracking
- Pack management (create, add stickers, remove, delete)
- Server-specific sticker packs with permissions

### Soundboard

- Server sound library
- Sound upload with audio validation (MP3/OGG under 5 seconds, under 512KB)
- Sound metadata (name, emoji, volume)
- Sound usage permissions per role
- Sound cooldowns per user
- Play sound in voice channel
- Usage tracking and statistics

### Embeds

- Rich embed creation with all standard fields
- Field limits and character validation
- URL preview embeds (OpenGraph/Twitter Card simulation)
- Attach/update/remove embeds from messages
- Suppress/unsuppress embeds (hide URL previews)
- Bot/webhook embeds vs user URL previews distinction
- XSS prevention and URL sanitization

### Relationships

- Friend requests (send, accept, decline, cancel)
- Block/unblock users
- Relationship states: none, friend, blocked, pending_incoming, pending_outgoing
- Get friends list
- Get blocked users list
- Get pending requests (incoming and outgoing)
- Check relationship between two users
- Mutual friends calculation
- Mutual servers calculation

### Presence

- Online/offline status
- Custom status messages
- Activity tracking
- Visibility rules

### Notifications

- Mention types: @user, @role, @everyone, @here, #channel
- Parse mentions from message content
- Validate mentions (user exists, role exists, has permission)
- Notification preferences per user (all messages, only mentions, nothing)
- Notification preferences per channel (override server)
- Get unread counts with mention counts
- Mark notifications as read
- Get notification feed (recent mentions across all servers)
- Highlight mentions in message
- Push notification hooks (prepare payload, don't send)

### Search

- Full-text message search with advanced query syntax
- User search by username/display name
- Server search by name/description/tags
- Public server directory with categories
- Multiple indexer backends (SQLite FTS5, Elasticsearch, Meilisearch)
- Incremental index updates on message create/edit/delete
- Permission-aware search (users only see messages they can access)
- Search result ranking by relevance

### Telemetry

- Opt-in client-side telemetry collection
- Anonymized collection (client IDs hashed)
- Batch submissions (up to 100 entries per request)
- Rate limiting to prevent abuse
- Aggregation (percentiles, averages, error rates)
- Time-series data bucketed by configurable intervals
- Auto-cleanup of old data

### Feedback

- User feedback submission
- Feedback categories and ratings
- Integration with admin dashboard for ticket management

### QR Codes

- Local QR code generation (no external dependencies)
- Privacy-focused QR generation
- Support for various QR content types

## Client Features

### Web Client (Flask-based)

- Modern web interface
- Session management via Flask
- Direct API calls to backend
- WebSocket connection to gateway
- IndexedDB caching for instant UI rendering
- OAuth support (Google, GitHub, Microsoft)
- 2FA support with QR code display
- Theme switching (Dark, Light, Midnight, Forest)
- Cloud-synced settings
- Mobile-responsive layout
- Browser notifications
- In-app toast notifications
- Settings page with 2FA management
- Session management (view/revoke sessions)
- Server address configuration

### Frontend JavaScript

- Modular architecture with direct API calls
- REST API client with caching and deduplication
- WebSocket handler for real-time events
- App state management with IndexedDB cache
- UI utilities (toast, modals, themes)
- Server/channel management
- Message handling and display
- Friend/relationship management
- Member list management
- Channel operations
- Voice/video/screen share (WebRTC)
- Browser and in-app notifications
- Settings page logic
- Telemetry collection

### Frontend Capabilities

- User registration and login
- 2FA with QR code support
- Thread support for organized conversations
- Mention parsing with highlighting and notifications
- Real-time gateway with WebSocket-based instant updates
- File attachments and media uploads
- Cloud-synced settings (theme, accent color, font size, compact mode)
- Customizable themes (Dark, Light, Midnight, Forest)
- Mobile-responsive layout with hamburger menu
- Voice channels with WebRTC (mute, deafen, speaking detection)
- Video calls with camera toggle
- Screen sharing support
- Message search with real-time results
- Pinned messages management
- Browser and in-app notifications with sounds
- Personal notes (always visible in friends list)
- Dedicated server settings page with roles, invites, bans, audit log
- Feedback submission system
- Privacy settings (DMs, friend requests, activity status)
- Tooltips on all interactive elements
- Compact message display mode
- Message reactions with emoji support
- Clickable usernames in messages to view profiles
- Dynamic file upload limits based on user tier
- Visual message editing mode indicator

## Shared Utilities (common-utils)

### Configuration

- YAML/JSON configuration loader
- Environment variable override support
- Configuration validation
- Multiple config file support

### Logger

- Standardized logging utility
- Color support
- File rotation
- Configurable log levels

### Validator

- Input validation helpers
- SQL injection protection
- XSS protection
- Custom validation rules

### Version

- Centralized version management
- Version comparison
- Version negotiation between client and server

## API Features

### REST API

- FastAPI-based REST API
- Interactive documentation (Swagger UI at /docs, ReDoc at /redoc)
- OpenAPI 3.0 schema
- Comprehensive endpoint coverage
- Request/response validation with Pydantic
- Error handling with consistent format
- Rate limiting middleware
- Authentication middleware
- CORS support
- Proxy header support (X-Forwarded-Proto, X-Forwarded-Host)

### WebSocket Gateway

- Real-time event delivery
- Session-based connections
- Heartbeat mechanism
- Resume support
- Intent filtering
- Compression support
- Voice signaling support
- Interaction support

### Security

- Content Security Policy (CSP)
- HTTP Strict Transport Security (HSTS)
- X-Frame-Options
- X-Content-Type-Options
- Referrer Policy
- Cross-Origin policies
- Secure cookie configuration
- CSP nonce generation per request

## Development & Testing

### Testing Framework

- Comprehensive test suite (3000+ tests across all repositories)
- pytest with pytest-asyncio
- Session-scoped database for fast execution
- User pool system with pre-created users
- Lazy module loading
- Security tests (XSS, SQL injection, CSRF, auth bypass, rate limiting)
- Integration tests (full module interaction)
- Unit tests (validators, utilities, property-based testing)
- Hypothesis-based property testing
- 85%+ coverage target

### Self-Test System

- Automated API validation suite
- Run on startup or via command-line
- Capture stack traces for debugging
- Retry on failure with debug headers
- Exclude specific endpoints
- Configurable test user

### Telemetry

- Client-side response time collection
- Anonymized data (hashed client IDs)
- Batch submissions
- Aggregation for admin dashboards
- Time-series data
- Opt-in only

## Configuration Options

### Server Configuration

- Database: SQLite or PostgreSQL
- Storage: Local filesystem, S3, MinIO
- Caching: Optional Redis
- Voice: Mediasoup or Janus SFU
- Search: SQLite FTS5, Elasticsearch, or Meilisearch
- Rate limiting: Per-user, per-IP, global
- Encryption: AES-256-GCM with key rotation
- Logging: Configurable levels, rotation, zipping
- CORS: Configurable origins and methods
- Proxy: Trusted proxy configuration

### Client Configuration

- Server address configuration
- OAuth provider configuration
- Security headers configuration
- Cookie configuration
- Theme configuration
- Font size configuration
- Compact mode configuration

## Versioning

- Stage-based versioning: `[stage].[major].[minor]-[build]`
- Stages: Alpha (a), Beta (b), Candidate (c), Release (r)
- Version negotiation between client and server
- Minimum supported version enforcement
- Update URL configuration

## Deployment

### Server

- Self-hosted deployment
- Docker support
- PostgreSQL for production
- Redis for caching
- S3/MinIO for media storage
- Reverse proxy support (Nginx, Apache)
- HTTPS/TLS support
- Health check endpoint
- Admin panel with 2FA requirement

### Client

- Flask-based web server
- Static file serving
- Session management
- OAuth callback handling
- Health check endpoint
- Reverse proxy support

## Summary

PlexiChat is a feature-rich real-time messaging platform with:

- **900+ test files** across all repositories
- **3000+ tests** with 85%+ coverage target
- **FastAPI backend** with WebSocket gateway
- **Flask web client** with modern frontend
- **Shared utilities** for configuration, logging, validation, and versioning
- **Comprehensive security** (encryption, rate limiting, input validation)
- **Voice & video** with WebRTC support
- **Moderation tools** (auto-moderation, admin panel)
- **Bot platform** with OAuth and slash commands
- **Server discovery** with public directory
- **Telemetry** for performance monitoring
- **Self-test system** for automated validation

All features are designed for scalability, security, and developer experience.
