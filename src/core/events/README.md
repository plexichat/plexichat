# Events Module

Event types, payloads, and routing for the Plexichat gateway system.

## Features

- Standard event types
- Gateway intents for event filtering
- Event payload builders
- Event routing to appropriate users
- Subscriber pattern for gateway integration

## Setup

```python
from src.core import events

# Basic setup
events.setup()

# With routing modules
events.setup(
    relationships_module=relationships,
    servers_module=servers,
    messaging_module=messaging,
)
```

## Usage

### Creating Events

```python
from src.core import events

# Message events
event = events.create_message_create(
    message_id=123,
    channel_id=456,
    author_id=789,
    content="Hello!",
    server_id=111,
)

# Presence events
event = events.create_presence_update(
    user_id=123,
    status="online",
    activities=[{"type": 0, "name": "Gaming"}],
)

# Typing events
event = events.create_typing_start(
    user_id=123,
    channel_id=456,
)
```

### Dispatching Events

```python
# Dispatch to specific users
events.dispatch(event, user_ids=[1, 2, 3])

# Dispatch to server members
events.dispatch(event, server_id=123)

# Dispatch to channel participants
events.dispatch(event, channel_id=456)

# Exclude specific users
events.dispatch(event, server_id=123, exclude_user_ids=[789])
```

### Subscribing to Events

```python
def on_event(event, user_ids):
    # Send to connected WebSocket clients
    for user_id in user_ids:
        send_to_user(user_id, event.to_dict())

events.subscribe(on_event)
```

### Intent Filtering

```python
from src.core.events import GatewayIntent

# Check if event passes intent filter
intents = GatewayIntent.GUILDS | GatewayIntent.GUILD_MESSAGES
if events.filter_by_intents(event, intents):
    # Send event
    pass

# Get required intent for event type
intent = events.get_required_intent(events.EventType.GUILD_MEMBER_ADD)
```

## Event Types

| Event | Description |
|-------|-------------|
| READY | Sent after successful identify |
| MESSAGE_CREATE | New message |
| MESSAGE_UPDATE | Message edited |
| MESSAGE_DELETE | Message deleted |
| PRESENCE_UPDATE | User presence changed |
| TYPING_START | User started typing |
| CHANNEL_CREATE | Channel created |
| CHANNEL_UPDATE | Channel updated |
| CHANNEL_DELETE | Channel deleted |
| GUILD_CREATE | Joined server or server available |
| GUILD_UPDATE | Server updated |
| GUILD_DELETE | Left server or server unavailable |
| GUILD_MEMBER_ADD | Member joined server |
| GUILD_MEMBER_REMOVE | Member left server |
| GUILD_MEMBER_UPDATE | Member updated |
| VOICE_STATE_UPDATE | Voice state changed |

## Gateway Intents

| Intent | Value | Events |
|--------|-------|--------|
| GUILDS | 1 << 0 | Guild events, channels, roles |
| GUILD_MEMBERS | 1 << 1 | Member add/remove/update |
| GUILD_BANS | 1 << 2 | Ban add/remove |
| GUILD_PRESENCES | 1 << 8 | Presence updates |
| GUILD_MESSAGES | 1 << 9 | Message events in guilds |
| GUILD_MESSAGE_REACTIONS | 1 << 10 | Reaction events in guilds |
| GUILD_MESSAGE_TYPING | 1 << 11 | Typing events in guilds |
| DIRECT_MESSAGES | 1 << 12 | DM message events |
| DIRECT_MESSAGE_REACTIONS | 1 << 13 | DM reaction events |
| DIRECT_MESSAGE_TYPING | 1 << 14 | DM typing events |
| MESSAGE_CONTENT | 1 << 15 | Message content (privileged) |

## Testing

```bash
pytest src/tests/events/ -v
```
