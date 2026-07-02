# WebSocket Handlers

Real-time event handlers for WebSocket connections. Each handler manages a specific domain of real-time communication, dispatching events between connected clients and the server.

## Handlers

### `connection.py`
Connection lifecycle management:
- Client connect/disconnect handling
- Authentication and session validation on connect
- Reconnection logic with session resume
- Heartbeat/ping-pong for connection health
- Rate limiting per connection
- Graceful disconnect with reason codes

### `guild.py`
Server/guild real-time events:
- Channel creation, update, deletion
- Member join, leave, role changes
- Server settings updates
- Permission changes
- Ban/unban notifications
- Channel visibility updates

### `presence.py`
Presence and activity tracking:
- Online/offline/idle/dnd status changes
- Custom status updates
- Activity tracking (playing, watching, listening)
- Typing indicators
- Friend presence updates
- Cross-server presence aggregation

### `voice.py`
Voice channel events:
- Voice channel join/leave/move
- Mute/deafen state changes
- Speaking indicator
- Voice stream quality changes
- Server deafen/mute by moderator
- Voice channel participant list updates

## Event Dispatch

Events are dispatched to connected clients based on their subscriptions:
- DM conversations: events go to both participants
- Group conversations: events go to all participants
- Server channels: events go to server members with channel access
- Presence: events go to friends and shared server members

## Error Handling

- Invalid events return a structured error response
- Rate-limited connections are throttled with backoff
- Unauthorized access attempts disconnect with auth error
- Malformed payloads return validation errors
