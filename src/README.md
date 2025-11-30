# Source Code

Main source code for the Plexichat server application.

## Structure

- `api/` - FastAPI application, routes, middleware, schemas, and WebSocket gateway
- `core/` - Core business logic modules (auth, messaging, servers, etc.)
- `tests/` - Test suite for all modules
- `utils/` - Shared utilities (encryption, common-utils)

## Architecture

The application follows a modular architecture where each feature is self-contained in its own module under `core/`. The `api/` layer handles HTTP/WebSocket requests and delegates to core modules.
