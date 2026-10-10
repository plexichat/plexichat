# Events tests

This directory contains tests for event dispatching, routing, type definitions,
and payload construction. The tests verify that events flow correctly from
creation through routing to subscriber delivery.

## Test files

| File | Description |
|------|-------------|
| `test_dispatch.py` | Event dispatch and subscriber management |
| `test_event_types.py` | `EventType` enum values and `GatewayIntent` bitflags |
| `test_payloads.py` | Payload factory functions for all event categories |
| `test_routing.py` | Intent mapping, intent filtering, and recipient routing |
| `test_critical_subscribers.py` | Critical subscriber delivery guarantees |
| `test_queued_delivery_integration.py` | Queued event delivery integration |

## What these tests cover

### Event dispatch (`test_dispatch.py`)

- Subscribe and unsubscribe callbacks from the event manager
- Dispatched events reach all registered subscribers
- Dispatch with no subscribers does not raise
- Dispatch returns the correct recipient count
- Dispatch with empty recipients returns 0
- `Event.to_dict()` serializes event type and data correctly
- `Event` objects carry a timestamp
- A failing subscriber does not prevent other subscribers from receiving events

### Event types (`test_event_types.py`)

- `EventType` enum values for messages, presence, guilds, members, channels,
  voice, reactions, and ready/resumed events
- `GatewayIntent` bitflags use correct power-of-two values
- `all_intents()` includes every intent flag
- `default_intents()` excludes privileged intents (`GUILD_MEMBERS`,
  `GUILD_PRESENCES`, `MESSAGE_CONTENT`)
- `privileged_intents()` returns only the privileged subset
- Intents combine with bitwise OR and check with bitwise AND

### Payloads (`test_payloads.py`)

- Message payloads: `create_message_create`, `create_message_update`,
  `create_message_delete` with optional server ID, attachments, and content
- Presence payloads: `create_presence_update` with status and activities;
  `create_typing_start` with optional server ID
- Channel payloads: `create_channel_create`, `create_channel_update`,
  `create_channel_delete` with type and name
- Guild payloads: `create_guild_create`, `create_guild_update`,
  `create_guild_delete` with name, owner, and member count
- Member payloads: `create_guild_member_add`, `create_guild_member_remove`,
  `create_guild_member_update` with nickname and roles
- Voice payloads: `create_voice_state_update` with mute/deaf flags and
  disconnect handling
- Reaction payloads: `create_reaction_add`, `create_reaction_remove`
  with emoji data
- Serialization: `Event.to_dict()` produces `{"t": ..., "d": ...}` format
  with string IDs in event data

### Routing (`test_routing.py`)

- **Intent mapping** -- `get_required_intent` returns the correct intent for
  each event type (guild, channel, member, presence, voice, reaction, typing)
- **DM intent mapping** -- `get_dm_intent` returns `DIRECT_MESSAGES` for DM
  message events and `DIRECT_MESSAGE_TYPING` for typing events
- **READY event** -- requires no intent (`None`)
- **Intent filtering** -- `filter_by_intents` passes or blocks events based on
  the subscriber's intent bitmask
- **Default intents** -- block privileged events (member add/remove) but allow
  guild and message events
- **Recipient routing** -- `EventRouter.get_recipients` uses explicit user IDs,
  excludes specified users, and falls back to empty when no routing modules are
  available

## Running the tests

```bash
# Run all events tests
pytest src/tests/events/ -v

# Run dispatch tests only
pytest src/tests/events/test_dispatch.py -v

# Run routing tests only
pytest src/tests/events/test_routing.py -v

# Run with coverage
pytest src/tests/events/ --cov=src.core.events -v
```

## Key classes and fixtures

- `TestDispatch` -- subscribe, unsubscribe, dispatch, and error isolation
- `TestEventType` -- `EventType` enum completeness
- `TestGatewayIntent` -- intent bitflags, combinations, and defaults
- `TestMessagePayloads`, `TestPresencePayloads`, `TestChannelPayloads`,
  `TestGuildPayloads`, `TestMemberPayloads`, `TestVoicePayloads`,
  `TestReactionPayloads` -- payload factory verification
- `TestEventToDict` -- serialization format
- `TestIntentMapping` -- event-to-intent mapping correctness
- `TestIntentFiltering` -- bitmask-based event filtering
- `TestDefaultIntents` -- privileged intent exclusion
- `TestEventRouter` -- recipient list computation
- `TestEventDispatch` -- module-level dispatch API

## Related source code

- `src/core/events/models.py` -- `Event` model
- `src/core/events/types.py` -- `EventType` and `GatewayIntent` enums
- `src/core/events/router.py` -- `EventRouter`, `get_required_intent`,
  `get_dm_intent`, `filter_by_intents`
- `src/core/events/__init__.py` -- payload factory functions
