# Applications Module

Application platform for PlexiChat supporting OAuth2 authorization, slash commands, interactions, and component builders.

## Features

- OAuth2 authorization flows (authorization code grant, bot auth flow)
- Application registration and management
- Slash command registration (global and per-server)
- Command option types (string, integer, boolean, user, channel, role, mentionable, number, attachment)
- Autocomplete support for command options
- Interaction handling (slash commands, buttons, select menus, modals, context menus)
- Interaction response types (channel message, ephemeral, deferred, modal, autocomplete)
- Component builders with validation
- Webhook-based interaction endpoint for serverless bots
- Rate limiting per application
- Application installation tracking per server

## Setup

```python
from src.core.database import Database
from src.core import auth, servers, events, applications

# Initialize database
db = Database()
db.connect()

# Initialize dependencies
auth.setup(db)
servers.setup(db, auth)
events.setup()

# Initialize applications
applications.setup(db, auth, servers, events)
```

## Usage

### Application Management

```python
from src.core import applications

# Create an application
app = applications.create_application(
    owner_id=user_id,
    name="My Bot",
    description="A cool bot",
    redirect_uris=["https://mybot.com/callback"],
)
print(f"Client ID: {app.id}")
print(f"Client Secret: {app.client_secret}")  # Only shown once!

# Get application
app = applications.get_application(app_id)

# Update application
app = applications.update_application(
    user_id=owner_id,
    application_id=app_id,
    name="Updated Name",
    description="New description",
)

# Delete application
applications.delete_application(owner_id, app_id)

# Regenerate client secret
new_secret = applications.regenerate_client_secret(owner_id, app_id)
```

### Bot Account

```python
# Create bot for application
bot_info = applications.create_bot_for_application(
    user_id=owner_id,
    application_id=app_id,
    permissions={"messages.send": True, "messages.read": True},
)
print(f"Bot Token: {bot_info['token']}")  # Only shown once!
```

### OAuth2 Authorization

```python
# Generate authorization URL
url = applications.generate_oauth_url(
    application_id=app_id,
    redirect_uri="https://mybot.com/callback",
    scopes=["identify", "guilds", "bot"],
    state="random_state",
    permissions="8",  # Administrator for bot
)

# Exchange code for tokens
tokens = applications.exchange_code(
    application_id=app_id,
    client_secret=client_secret,
    code=authorization_code,
    redirect_uri="https://mybot.com/callback",
)
print(f"Access Token: {tokens['access_token']}")
print(f"Refresh Token: {tokens['refresh_token']}")

# Refresh token
new_tokens = applications.refresh_token(
    application_id=app_id,
    client_secret=client_secret,
    refresh_token=tokens['refresh_token'],
)

# Revoke token
applications.revoke_token(tokens['access_token'])
```

### Slash Commands

```python
from src.core.applications import CommandType, CommandOptionType

# Register global command
cmd = applications.register_command(
    application_id=app_id,
    name="ping",
    description="Check bot latency",
)

# Register command with options
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
        {
            "name": "ephemeral",
            "description": "Only visible to you",
            "type": CommandOptionType.BOOLEAN,
            "required": False,
        },
    ],
)

# Register guild-specific command
cmd = applications.register_command(
    application_id=app_id,
    name="serverinfo",
    description="Get server information",
    server_id=server_id,
)

# Register context menu command
cmd = applications.register_command(
    application_id=app_id,
    name="Report Message",
    description="",
    command_type=CommandType.MESSAGE,
)

# Update command
cmd = applications.update_command(
    command_id=cmd.id,
    description="Updated description",
)

# Delete command
applications.delete_command(cmd.id)

# Get all commands
commands = applications.get_commands(app_id)
guild_commands = applications.get_commands(app_id, server_id=server_id)
```

### Interactions

```python
from src.core.applications import (
    InteractionType,
    create_message_response,
    create_deferred_response,
    create_modal_response,
    create_autocomplete_response,
)

# Handle incoming interaction
interaction = applications.handle_interaction(
    application_id=app_id,
    interaction_type=InteractionType.APPLICATION_COMMAND,
    user_id=user_id,
    data={"name": "ping", "type": 1},
    server_id=server_id,
    channel_id=channel_id,
)

# Respond with message
response = create_message_response(
    content="Pong!",
    ephemeral=False,
)
applications.create_interaction_response(interaction.token, response)

# Respond with ephemeral message
response = create_message_response(
    content="Only you can see this!",
    ephemeral=True,
)

# Deferred response (for long operations)
response = create_deferred_response(ephemeral=False)
applications.create_interaction_response(interaction.token, response)
# ... do work ...
# Then edit the original response via webhook

# Modal response
response = create_modal_response(
    custom_id="feedback_modal",
    title="Submit Feedback",
    components=[
        {
            "type": 1,
            "components": [
                {
                    "type": 4,
                    "custom_id": "feedback_text",
                    "label": "Your Feedback",
                    "style": 2,
                    "required": True,
                }
            ]
        }
    ],
)

# Autocomplete response
response = create_autocomplete_response(
    choices=[
        {"name": "Option 1", "value": "opt1"},
        {"name": "Option 2", "value": "opt2"},
    ]
)
```

### Components

```python
from src.core.applications import (
    build_button,
    build_select_menu,
    build_action_row,
    ButtonStyle,
    ComponentType,
)

# Build buttons
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

# Build select menu
select = build_select_menu(
    custom_id="color_select",
    component_type=ComponentType.STRING_SELECT,
    placeholder="Choose a color",
    options=[
        {"label": "Red", "value": "red", "emoji": {"name": "red_circle"}},
        {"label": "Blue", "value": "blue"},
        {"label": "Green", "value": "green"},
    ],
)

# Build action row
row = build_action_row([primary_btn, link_btn])

# Send message with components
response = create_message_response(
    content="Choose an option:",
    components=[
        {"type": 1, "components": [primary_btn.__dict__]},
        {"type": 1, "components": [select.__dict__]},
    ],
)
```

### Installation Tracking

```python
# Install application on server
installation = applications.install_application(
    application_id=app_id,
    server_id=server_id,
    installer_id=user_id,
    permissions="8",
    scopes=["bot", "applications.commands"],
)

# Get installations
app_installations = applications.get_installations(application_id=app_id)
server_installations = applications.get_installations(server_id=server_id)

# Uninstall application
applications.uninstall_application(app_id, server_id, user_id)
```

### Webhook Verification

```python
# Verify incoming webhook request
try:
    applications.verify_webhook_signature(
        body=request.body,
        signature=request.headers["X-Signature-Ed25519"],
        timestamp=request.headers["X-Signature-Timestamp"],
    )
except applications.WebhookSignatureError:
    return Response(status=401)
```

### Rate Limiting

```python
try:
    applications.check_rate_limit(app_id)
    # Process request
except applications.RateLimitError as e:
    return Response(
        status=429,
        headers={"Retry-After": str(e.retry_after)},
    )
```

## Configuration

Add to `config/config.yaml`:

```yaml
applications:
  max_applications_per_user: 25
  oauth:
    token_expiry_seconds: 604800  # 7 days
    code_expiry_seconds: 600  # 10 minutes
    refresh_enabled: true
    allowed_redirect_uri_pattern: "^https?://"
  command_limits:
    max_commands_per_app: 100
    max_options_per_command: 25
  interaction_timeout: 900  # 15 minutes
  rate_limits:
    requests_per_minute: 50
    burst_limit: 10
  webhook_signature_secret: "your-secret-key"
```

## OAuth2 Scopes

| Scope | Description |
|-------|-------------|
| identify | Access username, avatar, and banner |
| email | Access email address |
| guilds | Know what servers user is in |
| guilds.join | Join servers on user's behalf |
| guilds.members.read | Read member info in servers |
| bot | Add a bot to a server |
| applications.commands | Create commands in managed servers |
| applications.commands.update | Update application commands |
| messages.read | Read user's messages |
| webhook.incoming | Create webhooks in managed channels |

## Command Option Types

| Type | Value | Description |
|------|-------|-------------|
| SUB_COMMAND | 1 | Sub-command |
| SUB_COMMAND_GROUP | 2 | Sub-command group |
| STRING | 3 | String input |
| INTEGER | 4 | Integer input |
| BOOLEAN | 5 | Boolean choice |
| USER | 6 | User mention |
| CHANNEL | 7 | Channel mention |
| ROLE | 8 | Role mention |
| MENTIONABLE | 9 | User or role mention |
| NUMBER | 10 | Decimal number |
| ATTACHMENT | 11 | File attachment |

## Interaction Types

| Type | Value | Description |
|------|-------|-------------|
| PING | 1 | Webhook ping |
| APPLICATION_COMMAND | 2 | Slash command |
| MESSAGE_COMPONENT | 3 | Button/select interaction |
| APPLICATION_COMMAND_AUTOCOMPLETE | 4 | Autocomplete request |
| MODAL_SUBMIT | 5 | Modal form submission |

## Response Types

| Type | Value | Description |
|------|-------|-------------|
| PONG | 1 | Respond to ping |
| CHANNEL_MESSAGE_WITH_SOURCE | 4 | Send message |
| DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE | 5 | Acknowledge, send later |
| DEFERRED_UPDATE_MESSAGE | 6 | Acknowledge component, update later |
| UPDATE_MESSAGE | 7 | Update component message |
| APPLICATION_COMMAND_AUTOCOMPLETE_RESULT | 8 | Autocomplete choices |
| MODAL | 9 | Show modal dialog |

## Error Handling

```python
from src.core.applications import (
    ApplicationError,
    ApplicationNotFoundError,
    CommandValidationError,
    InteractionExpiredError,
    OAuth2Error,
    InvalidClientError,
    RateLimitError,
)

try:
    applications.create_interaction_response(token, response)
except InteractionExpiredError:
    print("Interaction token expired")
except ApplicationError as e:
    print(f"Application error: {e}")
```

## WebSocket Events

The module dispatches `INTERACTION_CREATE` events via the WebSocket gateway:

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

```bash
pytest src/tests/applications/ -v
```

## Database Schema

Tables (prefixed with `app_`):
- `app_applications` - Application metadata
- `app_commands` - Registered commands
- `app_installations` - Server installations
- `app_oauth_codes` - Authorization codes
- `app_oauth_tokens` - Access/refresh tokens
- `app_interactions` - Interaction records
- `app_webhook_deliveries` - Webhook delivery logs
