# PlexiChat Documentation

API and WebSocket documentation for the PlexiChat messaging platform.

## Structure

```
docs/
+-- index.md              # Documentation home
+-- getting-started.md    # Quick start guide
+-- configuration.md      # Server configuration
+-- data-types.md         # Data type definitions
+-- errors.md             # Error codes and handling
+-- rate-limits.md        # Rate limiting documentation
+-- performance.md        # Performance optimization guide
+-- api/                  # REST API documentation
|   +-- index.md          # Endpoint reference
|   +-- authentication.md # Auth endpoints
|   +-- users.md          # User management
|   +-- servers.md        # Server/guild management
|   +-- channels.md       # Channel management
|   +-- messages.md       # Messaging
|   +-- reactions.md      # Message reactions
|   +-- relationships.md  # Friends and blocks
|   +-- presence.md       # User status
|   +-- webhooks.md       # Webhook integration
|   +-- avatars.md        # Avatar management
|   +-- emojis.md         # Custom emoji
+-- websocket/            # WebSocket documentation
    +-- index.md          # Gateway overview
    +-- connection.md     # Connection lifecycle
    +-- events.md         # Event types
    +-- close-codes.md    # Close code reference
```

## Serving Documentation

Documentation is served through the API at `/docs/api/` when enabled in configuration.

### Configuration

```yaml
docs:
  enabled: true
  path: /docs/api
  title: PlexiChat API Documentation
  theme:
    style: dark
    primary_color: "#e94560"
```

## Contributing

When updating documentation:

1. Verify all claims against the actual codebase in `src/`
2. Keep examples accurate and tested
3. Update inter-document links when adding new pages
4. Follow the existing markdown formatting style
