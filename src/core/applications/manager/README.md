# Application manager

This document describes the application manager for Plexichat. Use it to manage the lifecycle of applications, bots, and integrations, including OAuth, commands, installations, and webhook verification.

## Overview

`ApplicationManager` is the central orchestrator for application logic. It composes behavior from focused mixins and delegates to three subcomponents:

*   `OAuth2Flow` — handles the authorization-code grant, token exchange, and refresh. Default expiry is 604800 seconds for tokens and 600 seconds for codes.
*   `CommandRegistry` — validates and stores slash commands. Default limit is 100 commands per application and 25 options per command.
*   `InteractionHandler` — validates and routes interaction payloads with a 900-second timeout.

The manager loads defaults and merges them with the `applications` config key at startup.

## Architecture

`ApplicationManager` inherits from `BaseManager` and six mixins. Each mixin owns a discrete area.

| File | Class or content | Responsibility |
| --- | --- | --- |
| `__init__.py` | Re-export | Re-exports `ApplicationManager` from `base.py`. |
| `base.py` | `ApplicationManager` | Initializes the manager, merges config, and creates `OAuth2Flow`, `CommandRegistry`, and `InteractionHandler`. |
| `application_crud.py` | `AppCRUDMixin` | Creates, reads, updates, and deletes applications, OAuth credentials, commands, and interactions. |
| `installations.py` | `InstallationMixin` | Installs, uninstalls, and retrieves application installations. |
| `bot_management.py` | `BotManagementMixin` | Approves, removes, and manages bot profile and directory requests. |
| `admin.py` | `AdminMixin` | Provides admin dashboard, bot, and feature statistics. |
| `webhook.py` | `WebhookMixin` | Verifies webhook signatures. |
| `ratelimit.py` | `RatelimitMixin` | Enforces per-application rate limiting. |
| `row_mappers.py` | Functions | Maps database rows to dataclasses, for example `row_to_application` and `row_to_installation`. |
| `protocol.py` | Protocol | Defines the interface shared across mixins. |

## Configuration

`ApplicationManager._load_config()` merges the following defaults with `config.get("applications", {})`:

| Key | Default | Description |
| --- | --- | --- |
| `max_applications_per_user` | `25` | Maximum applications a user can own. |
| `oauth.token_expiry_seconds` | `604800` | Lifetime of an access token. |
| `oauth.code_expiry_seconds` | `600` | Lifetime of an authorization code. |
| `oauth.refresh_enabled` | `true` | Whether refresh tokens are issued. |
| `oauth.allowed_redirect_uri_pattern` | `^https?://` | Regex that redirect URIs must match. |
| `command_limits.max_commands_per_app` | `100` | Maximum commands per application. |
| `command_limits.max_options_per_command` | `25` | Maximum options per command. |
| `interaction_timeout` | `900` | Timeout for interaction payloads in seconds. |
| `rate_limits.requests_per_minute` | `50` | Sustained rate limit. |
| `rate_limits.burst_limit` | `10` | Burst allowance. |
| `webhook_signature_max_skew_seconds` | `300` | Maximum allowed clock skew for webhook timestamps. |
| `webhook_signature_secret` | Generated | Per-installation secret. See webhook secret behavior. |

### Webhook secret behavior

`_resolve_webhook_secret()` does not use a static default. If `webhook_signature_secret` is unset or equals the legacy value `plexichat-webhook-secret`, the manager generates a random value with `secrets.token_urlsafe(48)`, persists it with `config.set("applications", ...)`, and retries up to 3 times on failure. If persistence fails after 3 attempts, startup raises `RuntimeError` instead of running with divergent in-memory secrets.

## Create and configure an application

### Prerequisites

*   A connected `db` instance.
*   Optional `auth_module`, `servers_module`, and `events_module` dependencies for permission checks, installation tracking, and interaction dispatch.

### Initialize the manager

```python
from src.core.applications import ApplicationManager

app_mgr = ApplicationManager(db, auth_module=auth, servers_module=servers, events_module=events)
```

### Create an application

```python
app = app_mgr.create_application(
    owner_id=1,
    name="My Bot",
    description="A cool bot",
    redirect_uris=["https://example.com/callback"],
)
```

### Register a command

```python
cmd = app_mgr.register_command(
    app_id=app.id,
    name="greet",
    description="Say hello",
    options=[{"name": "name", "type": 3, "description": "Who to greet", "required": True}],
)
```

### Verify a webhook

`verify_webhook_signature()` returns `True` or `False`. It does not raise an exception for invalid or expired signatures.

```python
from src.core.applications.manager import ApplicationManager

payload = b'{"type": 1}'
signature = request.headers.get("X-Signature-Ed25519")
timestamp = request.headers.get("X-Signature-Timestamp")
is_valid = app_mgr.verify_webhook_signature(payload, signature, timestamp)
```

## Error handling

Application operations raise exceptions that follow the auth module conventions:

*   `PermissionDeniedError` — The user does not have permission to manage the application.
*   `ValueError` — The input is invalid, for example a malformed redirect URI or an invalid OAuth scope.

```python
from src.core.auth.exceptions import PermissionDeniedError

try:
    app = app_mgr.create_application(owner_id=1, name="My Bot", redirect_uris=[])
except ValueError as e:
    print(f"Validation error: {e}")
```

Rate-limit violations are handled silently by `RatelimitMixin` using a token-bucket algorithm per application.

## Dependencies

*   Auth module — verifies bot ownership and access checks.
*   Servers module — tracks server-scoped installations.
*   Events module — dispatches interactions when enabled.
*   `OAuth2Flow`, `CommandRegistry`, and `InteractionHandler` — initialized in `ApplicationManager.__init__`.

## Related code

*   `plexichat/src/core/applications/manager/base.py:30` — `ApplicationManager.__init__`
*   `plexichat/src/core/applications/manager/base.py:69` — `_load_config`
*   `plexichat/src/core/applications/manager/base.py:97` — `_resolve_webhook_secret`
*   `plexichat/src/core/applications/manager/webhook.py` — `WebhookMixin`
*   `plexichat/src/core/applications/manager/ratelimit.py` — `RatelimitMixin`
