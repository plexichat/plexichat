# API Routes

This directory contains all FastAPI route handlers for the PlexiChat REST API.

## Files

| File | Description |
|------|-------------|
| `__init__.py` | Route registration and API router factory |
| `admin.py` | Admin panel endpoints |
| `auth.py` | Authentication (register, login, logout, 2FA) |
| `channels.py` | Channel management endpoints |
| `docs.py` | API documentation endpoints |
| `emojis.py` | Custom emoji endpoints |
| `features.py` | Feature flags endpoints |
| `feedback.py` | User feedback endpoints |
| `health.py` | Health check endpoint |
| `messages.py` | Message CRUD operations |
| `notifications.py` | Notification endpoints |
| `organizations.py` | Organization management |
| `presence.py` | User presence/status endpoints |
| `reactions.py` | Message reaction endpoints |
| `relationships.py` | Friend/block relationships |
| `servers.py` | Server management endpoints |
| `settings.py` | User settings endpoints |
| `telemetry.py` | Telemetry/analytics endpoints |
| `users.py` | User profile endpoints |
| `version.py` | API version endpoint |
| `webhooks.py` | Webhook management endpoints |

## Usage

Routes are registered via `create_api_router()` in `__init__.py` and mounted with the `/api/v1` prefix.
