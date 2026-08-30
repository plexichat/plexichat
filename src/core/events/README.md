# Events module

The events module defines gateway event types, payload builders, and routing logic for the PlexiChat gateway. Use this module to create events, dispatch them to users, and subscribe gateway integrations to event delivery.

## Before you begin

Initialize the module once at application startup before you create or dispatch events.

```python
from src.core import events

# Minimal setup
events.setup()

# With routing modules for server and channel resolution
events.setup(
    relationships_module=relationships,
    servers_module=servers,
    messaging_module=messaging,
)
```

If you call `events.dispatch()` or `events.subscribe()` before setup, the module raises `RuntimeError`.

## Create events

Use the factory functions in `payloads.py` to build typed events. Each factory returns an `Event` subclass defined in `models.py`.

```python
from src.core import events

# Message event
event = events.create_message_create(
    message_id=123,
    channel_id=456,
    author_id=789,
    content="Hello!",
    server_id=111,
)

# Presence event
event = events.create_presence_update(
    user_id=123,
    status="online",
    activities=[{"type": 0, "name": "Gaming"}],
)

# Typing event
event = events.create_typing_start(
    user_id=123,
    channel_id=456,
)

# Ready event
event = events.create_ready_event(
    session_id="abc",
    user={"id": 123},
    guilds=[],
)
```

Available factories in `payloads.py` and `__init__.py:39` include `create_ready_event`, `create_message_create`, `create_message_update`, `create_message_delete`, `create_presence_update`, `create_typing_start`, `create_channel_create`, `create_channel_update`, `create_channel_delete`, `create_guild_create`, `create_guild_update`, `create_guild_delete`, `create_guild_member_add`, `create_guild_member_remove`, `create_guild_member_update`, `create_guild_members_chunk`, `create_gateway_error`, `create_voice_state_update`, `create_reaction_add`, and `create_reaction_remove`.

You can also build low-level message events directly with helpers in `message_events.py`:

```python
from src.core.events import build_message_create, apply_server_scope
```

## Dispatch events

Call `events.dispatch()` to fan out an event to connected users. You can target specific users, all members of a server, or all participants in a channel.

```python
from src.core import events

# Dispatch to explicit user list
events.dispatch(event, user_ids=[1, 2, 3])

# Dispatch to all members of a server
events.dispatch(event, server_id=123)

# Dispatch to all participants in a channel
events.dispatch(event, channel_id=456)

# Exclude specific users from delivery
events.dispatch(event, server_id=123, exclude_user_ids=[789])
```

`dispatch()` is defined in `__init__.py:160` and delegated to `EventManager.dispatch()` in `manager.py`. It returns the number of users the event was dispatched to. Routing logic lives in `router.py`.

For multi-worker deployments, tag events with the current worker identity so siblings can skip their own echoes:

```python
events.set_worker_id("worker-1")
```

## Subscribe to events

Register a callback to receive dispatched events. The gateway typically uses this to forward events to WebSocket clients.

```python
from src.core import events

def on_event(event, user_ids):
    for user_id in user_ids:
        send_to_user(user_id, event.to_dict())

events.subscribe(on_event)

# Mark a subscriber as critical for non-lossy delivery
events.subscribe(on_event, critical=True)

# Unsubscribe when done
events.unsubscribe(on_event)
```

- **Regular subscribers** are invoked asynchronously through a queue. If the queue is full, events may be dropped. Use this lane for non-critical consumers.
- **Critical subscribers** (`critical=True`) are invoked synchronously before the queue, so a saturated queue cannot drop their events. Use this lane for real-time user-facing delivery such as the WebSocket gateway. Critical callbacks must be fast and non-blocking, and must not call `events.dispatch()` synchronously or the dispatch lock will deadlock. See `__init__.py:189` and `manager.py`.

Start and stop the async consumer on your application lifespan:

```python
await events.start_consumer()
await events.stop_consumer()
```

Check initialization state:

```python
if events.is_setup():
    events.dispatch(event, user_ids=[1])
```

## Filter by gateway intents

Gateway intents control which events a client receives. Use `GatewayIntent` in `types.py:97` to filter events.

```python
from src.core.events import GatewayIntent

intents = GatewayIntent.GUILDS | GatewayIntent.GUILD_MESSAGES

# Check whether an event should be sent for these intents
if events.filter_by_intents(event, intents):
    send_to_user(user_id, event.to_dict())

# Find the intent required for a specific event type
intent = events.get_required_intent(events.EventType.GUILD_MEMBER_ADD)
```

`get_required_intent()` and `filter_by_intents()` are defined in `__init__.py:225` and implemented in `router.py`.

## Event types

Event types are defined in `types.py:8` as `EventType`.

| Event | Description |
| --- | --- |
| `READY` | Sent after successful identify |
| `RESUMED` | Sent after successful resume |
| `ERROR` | Server error for a specific request |
| `MESSAGE_CREATE` | New message |
| `MESSAGE_UPDATE` | Message edited |
| `MESSAGE_DELETE` | Message deleted |
| `MESSAGE_DELETE_BULK` | Multiple messages deleted |
| `PRESENCE_UPDATE` | User presence changed |
| `TYPING_START` | User started typing |
| `TYPING_STOP` | User stopped typing |
| `CHANNEL_CREATE` | Channel created |
| `CHANNEL_UPDATE` | Channel updated |
| `CHANNEL_DELETE` | Channel deleted |
| `GUILD_CREATE` | Joined server or server available |
| `GUILD_UPDATE` | Server updated |
| `GUILD_DELETE` | Left server or server unavailable |
| `GUILD_MEMBER_ADD` | Member joined server |
| `GUILD_MEMBER_REMOVE` | Member left server |
| `GUILD_MEMBER_UPDATE` | Member updated |
| `VOICE_STATE_UPDATE` | Voice state changed |
| `GUILD_ROLE_CREATE` | Role created |
| `GUILD_ROLE_UPDATE` | Role updated |
| `GUILD_ROLE_DELETE` | Role deleted |
| `RELATIONSHIP_ADD` | Relationship added |
| `RELATIONSHIP_REMOVE` | Relationship removed |
| `WORKBOOK_UPDATE` | Workbook cell or structure change |

For the full list, see `types.py:8`.

## Gateway intents

Gateway intents are defined in `types.py:97` as `GatewayIntent`.

| Intent | Value | Events |
| --- | --- | --- |
| `GUILDS` | `1 << 0` | Guild events, channels, roles |
| `GUILD_MEMBERS` | `1 << 1` | Member add, remove, and update (privileged) |
| `GUILD_BANS` | `1 << 2` | Ban add and remove |
| `GUILD_PRESENCES` | `1 << 8` | Presence updates (privileged) |
| `GUILD_MESSAGES` | `1 << 9` | Message events in guilds |
| `GUILD_MESSAGE_REACTIONS` | `1 << 10` | Reaction events in guilds |
| `GUILD_MESSAGE_TYPING` | `1 << 11` | Typing events in guilds |
| `DIRECT_MESSAGES` | `1 << 12` | DM message events |
| `DIRECT_MESSAGE_REACTIONS` | `1 << 13` | DM reaction events |
| `DIRECT_MESSAGE_TYPING` | `1 << 14` | DM typing events |
| `MESSAGE_CONTENT` | `1 << 15` | Message content (privileged) |

Use `GatewayIntent.all_intents()` to get the combined mask of all intents, `GatewayIntent.default_intents()` for non-privileged defaults, and `GatewayIntent.privileged_intents()` for intents that require approval. See `types.py:123`.

## Test

```bash
pytest src/tests/events/ -v
```
