# PlexiChat Server Source

Main source code for the PlexiChat server application.

## Directory Structure

```
src/
├── api/           # FastAPI application and routes
│   ├── middleware/    # Request middleware (auth, rate limiting, etc.)
│   ├── routes/        # API endpoint handlers
│   ├── schemas/       # Pydantic request/response models
│   └── websocket/     # WebSocket gateway implementation
├── core/          # Core business logic modules
│   ├── auth/          # Authentication and authorization
│   ├── database/      # Database abstraction layer
│   ├── events/        # Event system for real-time updates
│   ├── messaging/     # Message handling
│   ├── servers/       # Server/guild management
│   ├── presence/      # User presence tracking
│   ├── relationships/ # Friend/block management
│   ├── reactions/     # Message reactions
│   ├── webhooks/      # Webhook system
│   ├── media/         # File upload and processing
│   └── ...            # Additional feature modules
├── tests/         # Test suites
└── utils/         # Shared utilities (submodule)
```

## Key Components

### API Layer (`api/`)

The FastAPI application that handles HTTP requests and WebSocket connections.

- `app.py` - FastAPI application setup
- `config.py` - API configuration
- `dependencies.py` - Dependency injection

### Core Modules (`core/`)

Business logic organized by feature domain. Each module typically contains:

- `__init__.py` - Module initialization and exports
- `manager.py` - Main business logic
- `models.py` - Data models
- `schema.py` - Validation schemas
- `exceptions.py` - Module-specific exceptions

### Utilities (`utils/`)

Shared utilities provided as a git submodule from `common-utils`:

- Configuration management
- Logging
- Validation helpers
- Version handling

## Getting Started

```bash
# Install dependencies
pip install -r requirements.txt

# Run the server
python main.py
```

## Configuration

Server configuration is loaded from:
1. `config/config.yaml` (project directory)
2. `~/.plexichat/config/config.yaml` (user directory)

See `docs/configuration.md` for detailed options.
