# Application Manager

## Purpose
Implements application lifecycle management, including OAuth, command
registration, and interaction handling for bots and integrations.

## File Structure

| File | Class/Content | Responsibility |
|------|--------------|----------------|
| `__init__.py` | — | Thin re-export of `ApplicationManager` from `base.py` |
| `base.py` | `ApplicationManager` | Class skeleton; `__init__`, `_load_config`, DI setup |
| `application_crud.py` | `AppCRUDMixin` | CRUD operations for applications, OAuth, commands, interactions |
| `installations.py` | `InstallationMixin` | Install/uninstall/get application installations |
| `bot_management.py` | `BotManagementMixin` | Bot approve/remove/request/profile/directory |
| `admin.py` | `AdminMixin` | Admin dashboard stats, bot stats, feature stats |
| `webhook.py` | `WebhookMixin` | Webhook signature verification |
| `ratelimit.py` | `RatelimitMixin` | Per-application rate limiting |
| `row_mappers.py` | Standalone functions | `row_to_application`, `row_to_installation`, etc. |

## Primary Responsibilities
- Create, update, and delete applications
- Issue and validate OAuth client credentials
- Manage application installations and permissions
- Register and validate application commands
- Dispatch and validate interaction payloads

## Core Components
- ApplicationManager: main orchestration class for application logic.
- OAuth2Flow: authorization code and token exchange handling (token expiry 604800s default, code expiry 600s).
- CommandRegistry: command validation and storage (max 100 commands per app, 25 options per command).
- InteractionHandler: interaction validation and routing (900s timeout).

## Usage

```python
from src.core.applications import ApplicationManager

app_mgr = ApplicationManager(db, auth_module=auth, servers_module=servers)

# Create an application
app = app_mgr.create_application(
    owner_id=1,
    name="My Bot",
    description="A cool bot",
    redirect_uris=["https://example.com/callback"]
)

# Register a slash command
cmd = app_mgr.register_command(
    app_id=app.id,
    name="greet",
    description="Say hello",
    options=[{"name": "name", "type": 3, "description": "Who to greet", "required": True}]
)

# Verify a webhook signature
from src.core.applications.manager import ApplicationManager
payload = b'{"type": 1}'
signature = request.headers.get("X-Signature-Ed25519")
timestamp = request.headers.get("X-Signature-Timestamp")
is_valid = app_mgr.verify_webhook_signature(payload, signature, timestamp)
```

## Error Handling

Application operations raise exceptions consistent with the auth module patterns:

- `PermissionDeniedError` — User lacks permission to manage the application.
- `ValueError` — Invalid input (e.g., malformed redirect URI, invalid OAuth scope).
- Rate-limit violations return silently via the `RatelimitMixin` (token bucket per application).

```python
from src.core.auth.exceptions import PermissionDeniedError

try:
    app = app_mgr.create_application(owner_id=1, name="My Bot", redirect_uris=[])
except ValueError as e:
    print(f"Validation error: {e}")
```

Webhook signature verification returns `False` for invalid or expired signatures — no exception is raised.

## Dependencies
- Core auth module for bot ownership and access checks.
- Servers module for installation tracking (server-scoped installations).
- Events module for interaction dispatch when enabled.
- OAuth2Flow, CommandRegistry, InteractionHandler subcomponents initialized in `__init__`.

## Configuration
- Command limits and timeouts are loaded from the `applications` config key.
- OAuth expiration (token: 604800s, code: 600s) and redirect URI pattern validation are configurable.
- Rate limits: 50 requests/minute default with 10 burst limit.
- Webhook signature secret: `plexichat-webhook-secret` default (override via config).
