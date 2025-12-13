# WebSocket Gateway

WebSocket gateway implementation for real-time event delivery.

## Structure

| File | Description |
|------|-------------|
| `__init__.py` | Module exports and setup functions |
| `gateway.py` | Main WebSocket handler |
| `connection.py` | Connection management |
| `dispatcher.py` | Event dispatching to clients |
| `handlers.py` | Opcode handlers |
| `session.py` | Session state management |
| `opcodes.py` | Opcode definitions |
| `intents.py` | Gateway intent flags |
| `compression.py` | Payload compression |

## Key Components

### Gateway (`gateway.py`)

Main WebSocket endpoint handler:
- Connection acceptance
- Message routing
- Heartbeat management
- Error handling

### Connection (`connection.py`)

Individual connection management:
- Authentication state
- Heartbeat tracking
- Send/receive operations
- Connection cleanup

### Dispatcher (`dispatcher.py`)

Event broadcasting:
- User-targeted dispatch
- Server-wide broadcast
- Intent filtering
- Rate limiting

### Session (`session.py`)

Session state:
- User sessions
- Resume support
- Event replay buffer

### Handlers (`handlers.py`)

Opcode-specific handlers:
- IDENTIFY
- HEARTBEAT
- RESUME
- PRESENCE_UPDATE
- Voice opcodes

## Usage

### Setup

```python
from src.api.websocket import setup, is_setup

# Initialize gateway
setup(auth_module, presence_module, ...)

# Check if ready
if is_setup():
    # Gateway is ready
    pass
```

### Dispatching Events

```python
from src.api.websocket import get_dispatcher
from src.core.events.models import Event
from src.core.events.types import EventType

dispatcher = get_dispatcher()

event = Event(
    event_type=EventType.MESSAGE_CREATE,
    data={"id": "123", "content": "Hello"},
    server_id=server_id,
    channel_id=channel_id
)

# Dispatch to specific users
await dispatcher.dispatch_event(event, [user_id1, user_id2])

# Dispatch to server members
await dispatcher.dispatch_to_server(event, server_id)
```

## Connection Flow

1. Client connects to `/gateway`
2. Server sends HELLO with heartbeat interval
3. Client sends IDENTIFY with token
4. Server validates and sends READY
5. Client begins heartbeat loop
6. Server dispatches events as they occur

## Intents

Clients specify which events they want via intents:

```python
from src.api.websocket.intents import GatewayIntents

intents = GatewayIntents.GUILDS | GatewayIntents.GUILD_MESSAGES
```

## Rate Limiting

- 120 events per 60 seconds per connection
- Exceeding limit closes with code 4008
