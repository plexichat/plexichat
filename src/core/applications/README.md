# Applications

Build and manage bot applications with OAuth2 authorization, slash commands, interactions, and message components.

## Overview

The applications module provides a platform for registering external applications, authorizing users through OAuth2, registering global and guild-specific slash commands, handling user interactions (commands, buttons, selects, modals), and tracking installations on servers. You initialize the module once during server startup and then use its public API throughout the codebase.

The module stores client secrets, tokens, and authorization codes as hashes. It validates redirect URIs against a registered allowlist and encrypts interaction payloads at rest.

## Before you begin

Ensure you have:

* A connected `Database` instance.
* An `auth` module if you create bot accounts.
* A `servers` module if you track application installations.
* An `events` module if you dispatch `INTERACTION_CREATE` events.

## Set up the module

Initialize the module once in your server entry point:

```python
from src.core.database import Database
from src.core import auth, servers, events, applications

db = Database()
db.connect()

auth.setup(db)
servers.setup(db, auth)
events.setup()

applications.setup(db, auth, servers, events)
```

## Use the module

### Manage an application

```python
from src.core import applications

# Create an application. The client secret is shown only once.
app = applications.create_application(
    owner_id=user_id,
    name="My Bot",
    description="A cool bot",
    redirect_uris=["https://mybot.com/callback"],
)
print(app.id)
print(app.client_secret)

# Retrieve and update an application
app = applications.get_application(app_id)
app = applications.update_application(
    user_id=owner_id,
    application_id=app_id,
    name="Updated Name",
)

# Regenerate the client secret and delete an application
new_secret = applications.regenerate_client_secret(owner_id, app_id)
applications.delete_application(owner_id, app_id)
```

### Create a bot account

```python
bot_info = applications.create_bot_for_application(
    user_id=owner_id,
    application_id=app_id,
    permissions={"messages.send": True},
)
print(bot_info["token"])  # Shown only once
```

### Authorize with OAuth2

```python
# Generate the authorization URL
url = applications.generate_oauth_url(
    application_id=app_id,
    redirect_uri="https://mybot.com/callback",
    scopes=["identify", "guilds", "bot"],
    state="random_state",
    permissions="8",
)

# Exchange an authorization code for tokens
tokens = applications.exchange_code(
    application_id=app_id,
    client_secret=client_secret,
    code=authorization_code,
    redirect_uri="https://mybot.com/callback",
)

# Refresh and revoke tokens
new_tokens = applications.refresh_token(
    application_id=app_id,
    client_secret=client_secret,
    refresh_token=tokens["refresh_token"],
)
applications.revoke_token(tokens["access_token"])
```

Authorization codes are single-use and bound to the redirect URI and application. Access tokens expire after `token_expiry_seconds` (default 7 days) and refresh tokens are revoked after use.

### Register slash commands

```python
from src.core.applications import CommandType, CommandOptionType

# Register a global chat-input command
cmd = applications.register_command(
    application_id=app_id,
    name="ping",
    description="Check bot latency",
)

# Register a command with options
cmd = applications.register_command(
    application_id=app_id,
    name="echo",
    description="Echo a message",
    options=[
        {
            "name": "message",
            "description": "Message to echo",
            "type": CommandOptionType.STRING,
            "required": True,
        },
    ],
)

# Register a guild-specific command
cmd = applications.register_command(
    application_id=app_id,
    name="serverinfo",
    description="Get server information",
    server_id=server_id,
)

# Register a context-menu command. Names preserve original casing.
cmd = applications.register_command(
    application_id=app_id,
    name="Report Message",
    description="",
    command_type=CommandType.MESSAGE,
)

# Update, list, and delete commands
cmd = applications.update_command(cmd.id, description="Updated description")
commands = applications.get_commands(app_id, server_id=server_id)
applications.delete_command(cmd.id)
```

Chat-input names are lowercased on write and must match `^[\w-]{1,32}$`. Context-menu names may include spaces and must match `^[\w\s-]{1,32}$`. See `commands/validation.py:11` for limits: 32 characters for names, 100 characters for descriptions, 25 options per command.

### Handle interactions

```python
from src.core.applications import InteractionType, create_message_response, create_deferred_response

interaction = applications.handle_interaction(
    application_id=app_id,
    interaction_type=InteractionType.APPLICATION_COMMAND,
    user_id=user_id,
    data={"name": "ping", "type": 1},
    server_id=server_id,
    channel_id=channel_id,
)

# Reply with a message
response = create_message_response(content="Pong!", ephemeral=False)
applications.create_interaction_response(interaction.token, response)

# Defer a reply for long-running work
response = create_deferred_response(ephemeral=False)
applications.create_interaction_response(interaction.token, response)
```

Interaction tokens expire after 15 minutes (`interaction_timeout` in `interactions/handler.py:35`). Responding a second time raises `InteractionAlreadyRespondedError`.

### Build message components

```python
from src.core.applications import build_button, build_select_menu, build_action_row, ButtonStyle, ComponentType

primary_btn = build_button(
    style=ButtonStyle.PRIMARY,
    label="Click Me",
    custom_id="btn_click",
)
link_btn = build_button(
    style=ButtonStyle.LINK,
    label="Visit Website",
    url="https://example.com",
)
select = build_select_menu(
    custom_id="color_select",
    component_type=ComponentType.STRING_SELECT,
    placeholder="Choose a color",
    options=[
        {"label": "Red", "value": "red"},
        {"label": "Blue", "value": "blue"},
    ],
)
row = build_action_row([primary_btn, link_btn])
```

### Track installations

```python
installation = applications.install_application(
    application_id=app_id,
    server_id=server_id,
    installer_id=user_id,
    permissions="8",
    scopes=["bot", "applications.commands"],
)

# List and remove installations
app_installs = applications.get_installations(application_id=app_id)
server_installs = applications.get_installations(server_id=server_id)
applications.uninstall_application(app_id, server_id, user_id)
```

## OAuth2 scopes

Scopes are space-separated strings. The module validates scopes against `VALID_SCOPES` in `oauth/scopes.py:8` and returns an error when the set contains unknown scopes or is empty.

| Scope | Description | Notes |
|---|---|---|
| `identify` | Access username, avatar, and banner. | — |
| `email` | Access email address. | — |
| `guilds` | Know which servers the user is in. | — |
| `guilds.join` | Join servers on the user's behalf. | — |
| `guilds.members.read` | Read the user's member information in servers. | Privileged |
| `bot` | Add a bot to a server. | Required when you request bot permissions |
| `applications.commands` | Create commands in servers the user manages. | — |
| `applications.commands.update` | Update application commands. | — |
| `messages.read` | Read the user's messages. | Privileged |
| `webhook.incoming` | Create webhooks in channels the user manages. | — |

* **Privileged scopes** are defined in `oauth/scopes.py:36` as `guilds.members.read` and `messages.read`. Only approved applications can request privileged scopes.
* **Bot scopes** – When you include `bot` in the authorization request, you can also pass `permissions`. The `bot` scope is tracked in `BOT_REQUIRED_SCOPES` in `oauth/scopes.py:34`.
* **Validation** – `validate_scopes` in `oauth/scopes.py:42` requires at least one scope and rejects invalid scopes. `parse_scopes` and `scopes_to_string` normalize scope sets.

To parse or serialize scopes:

```python
from src.core.applications import parse_scopes, scopes_to_string, validate_scopes

scopes = parse_scopes("identify guilds bot")
valid, issues = validate_scopes(scopes)
scope_string = scopes_to_string(scopes)  # Sorted, space-joined
```

## Command option types

| Type | Value | Description |
|---|---|---|
| `SUB_COMMAND` | `1` | Sub-command |
| `SUB_COMMAND_GROUP` | `2` | Sub-command group |
| `STRING` | `3` | String input |
| `INTEGER` | `4` | Integer input |
| `BOOLEAN` | `5` | Boolean choice |
| `USER` | `6` | User mention |
| `CHANNEL` | `7` | Channel mention |
| `ROLE` | `8` | Role mention |
| `MENTIONABLE` | `9` | User or role mention |
| `NUMBER` | `10` | Decimal number |
| `ATTACHMENT` | `11` | File attachment |

## Interaction types

| Type | Value | Description |
|---|---|---|
| `PING` | `1` | Webhook ping |
| `APPLICATION_COMMAND` | `2` | Slash command |
| `MESSAGE_COMPONENT` | `3` | Button or select interaction |
| `APPLICATION_COMMAND_AUTOCOMPLETE` | `4` | Autocomplete request |
| `MODAL_SUBMIT` | `5` | Modal form submission |

## Response types

| Type | Value | Description |
|---|---|---|
| `PONG` | `1` | Respond to a ping |
| `CHANNEL_MESSAGE_WITH_SOURCE` | `4` | Send a message |
| `DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE` | `5` | Acknowledge and send later |
| `DEFERRED_UPDATE_MESSAGE` | `6` | Acknowledge a component and update later |
| `UPDATE_MESSAGE` | `7` | Update the component message |
| `APPLICATION_COMMAND_AUTOCOMPLETE_RESULT` | `8` | Return autocomplete choices (maximum 25) |
| `MODAL` | `9` | Show a modal dialog |

## Configuration

Add the following keys to `config/config.yaml`:

```yaml
applications:
  max_applications_per_user: 25
  oauth:
    token_expiry_seconds: 604800  # 7 days
    code_expiry_seconds: 600  # 10 minutes
    refresh_enabled: true
    allowed_redirect_uri_pattern: "^https?://"
    authorization_endpoint: "/oauth2/authorize"
  command_limits:
    max_commands_per_app: 100
    max_options_per_command: 25
  interaction_timeout: 900  # 15 minutes
  rate_limits:
    requests_per_minute: 50
    burst_limit: 10
  webhook_signature_secret: "your-secret-key"
```

## Error handling

```python
from src.core.applications import (
    ApplicationError,
    CommandValidationError,
    InteractionExpiredError,
    InvalidScopeError,
    RateLimitError,
)

try:
    applications.create_interaction_response(token, response)
except InteractionExpiredError:
    print("Interaction token expired (15 minute window)")
except InvalidScopeError as exc:
    print(f"Invalid scopes: {exc}")
except ApplicationError as exc:
    print(f"Application error: {exc}")
```

All OAuth errors inherit from `OAuth2Error`. Command validation errors include an `issues` list with human-readable details.

## WebSocket events

The module dispatches `INTERACTION_CREATE` events through the gateway when an interaction is created:

```json
{
  "op": 0,
  "t": "INTERACTION_CREATE",
  "s": 42,
  "d": {
    "id": "123456789",
    "application_id": "987654321",
    "type": 2,
    "data": {
      "id": "111222333",
      "name": "ping",
      "type": 1
    },
    "guild_id": "444555666",
    "channel_id": "777888999",
    "user": {"id": "123123123"},
    "token": "interaction_token",
    "version": 1
  }
}
```

## Testing

Run the application tests with `pytest`:

```bash
pytest src/tests/applications/ -v
```

## Database schema

Tables are prefixed with `app_` and are defined in `schema.py:10`:

* `app_applications` – Application metadata and hashed client secrets.
* `app_commands` – Registered commands with a `UNIQUE(application_id, name, server_id)` constraint.
* `app_installations` – Server installations.
* `app_oauth_codes` – Authorization codes (hashed, single-use).
* `app_oauth_tokens` – Access and refresh token hashes.
* `app_interactions` – Interaction records with encrypted payloads.
* `app_webhook_deliveries` – Webhook delivery logs.
