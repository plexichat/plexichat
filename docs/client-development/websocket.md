# WebSocket gateway guide

The Plexichat WebSocket gateway streams real-time events for messages, presence, typing indicators, voice, and collaborative fabrics.

## Gateway URL

Use the `/gateway` path on your API server.

```
# Production
wss://api.plexichat.com/gateway

# Development
ws://localhost:8000/gateway
```

For the hosted deployment, the web client runs on `app.plexichat.com` and the API and gateway run on `api.plexichat.com`.

## Connection flow

### 1. Establish a connection and identify

```javascript
const ws = new WebSocket('wss://api.plexichat.com/gateway');

ws.onopen = () => {
  ws.send(JSON.stringify({
    op: 2, // Identify
    d: {
      token: 'your_session_token_here',
      properties: {
        os: 'windows',
        browser: 'chrome',
        device: 'desktop'
      },
      intents: 3178237
    }
  }));
};
```

`3178237` is the default non-privileged intent set. It includes `ARTIFACTS` and `SUMS`. See [Intents](#intents).

### 2. Handle gateway payloads

```javascript
ws.onmessage = (event) => {
  const payload = JSON.parse(event.data);

  switch (payload.op) {
    case 0:  // Dispatch
      handleDispatch(payload.t, payload.d);
      break;
    case 1:  // Heartbeat (server requests heartbeat)
      sendHeartbeat();
      break;
    case 10: // Hello
      startHeartbeat(payload.d.heartbeat_interval);
      break;
    case 11: // Heartbeat ACK
      lastHeartbeatAck = Date.now();
      break;
    case 9:  // Invalid session
      handleInvalidSession();
      break;
    case 7:  // Reconnect
      ws.close();
      break;
  }
};

function handleDispatch(eventType, data) {
  switch (eventType) {
    case 'MESSAGE_CREATE':
      console.log('New message:', data);
      break;
    case 'PRESENCE_UPDATE':
      console.log('Presence update:', data);
      break;
    case 'TYPING_START':
      console.log('User typing:', data);
      break;
  }
}
```

### 3. Send heartbeats

The server sends `Hello` (op `10`) with `heartbeat_interval` in milliseconds. Send a heartbeat at that interval.

```javascript
let heartbeatInterval;
let sequence = 0;

function startHeartbeat(interval) {
  heartbeatInterval = setInterval(() => {
    ws.send(JSON.stringify({
      op: 1,
      d: sequence
    }));
  }, interval);
}
```

Track `payload.s` on every dispatch so you can resume after a dropped connection. See [Connection resilience](#connection-resilience).

## Gateway opcodes

Values are defined in `plexichat/src/api/websocket/opcodes.py`.

| Opcode | Name | Description | Sent by |
| --- | --- | --- | --- |
| 0 | Dispatch | Event dispatch | Server |
| 1 | Heartbeat | Keep-alive ping | Client |
| 2 | Identify | Authenticate and start a session | Client |
| 3 | Presence Update | Update user presence | Client |
| 4 | Voice State Update | Join or leave a voice channel | Client |
| 6 | Resume | Resume a dropped session | Client |
| 7 | Reconnect | Server requests reconnect | Server |
| 8 | Request Guild Members | Request member list | Client |
| 9 | Invalid Session | Session is not resumable as sent | Server |
| 10 | Hello | Initial handshake with heartbeat interval | Server |
| 11 | Heartbeat ACK | Heartbeat acknowledgment | Server |
| 12 | Server Status | Server status update | Server |
| 13 | Version Check | Client version check | Client |
| 20 | Voice Connect | Initiate voice connection | Client |
| 21 | Voice Disconnect | End voice connection | Client |
| 22 | Voice SDP Offer | WebRTC offer | Client or server |
| 23 | Voice SDP Answer | WebRTC answer | Client or server |
| 24 | Voice ICE Candidate | ICE candidate exchange | Client or server |
| 25 | Voice Speaking | Speaking state update | Client or server |
| 26 | Voice Quality | Quality metrics | Server |
| 30 | Interaction Create | Application interaction | Server |
| 31 | Interaction Response | Interaction response | Client |
| 40 | Typing Start | User started typing | Server |
| 41 | Typing Stop | User stopped typing | Server |
| 50 | Ratchet Update | Channel encryption ratchet key update | Client or server |
| 60 | Artifact Subscribe | Subscribe to an artifact | Client |
| 61 | Artifact Unsubscribe | Unsubscribe from an artifact | Client |
| 62 | Artifact Op | Apply an operation to an artifact | Client or server |
| 63 | Artifact Sync | Request or receive full artifact state | Client or server |
| 64 | Sum Subscribe | Subscribe to a PlexiSum workbook | Client |
| 65 | Sum Unsubscribe | Unsubscribe from a workbook | Client |
| 66 | Sum Op | Apply an operation to a workbook | Client or server |
| 67 | Sum Sync | Request or receive full workbook state | Client or server |
| 68 | Artifact Op Rejected | A stale artifact operation was refused; re-sync and re-base | Server |

Artifacts are collaborative canvases such as whiteboards and live code editors. Sums are collaborative PlexiSum workbooks. Each family uses the subscribe, op, and sync shape.

## Intents

Intents are a bitmask that controls which events you receive. Values are defined in `plexichat/src/core/events/types.py`.

```javascript
const Intents = {
  GUILDS: 1 << 0,                    // 1
  GUILD_MEMBERS: 1 << 1,             // 2 - privileged
  GUILD_BANS: 1 << 2,                // 4
  GUILD_EMOJIS: 1 << 3,              // 8
  GUILD_INTEGRATIONS: 1 << 4,        // 16
  GUILD_WEBHOOKS: 1 << 5,            // 32
  GUILD_INVITES: 1 << 6,             // 64
  GUILD_VOICE_STATES: 1 << 7,        // 128
  GUILD_PRESENCES: 1 << 8,           // 256 - privileged
  GUILD_MESSAGES: 1 << 9,            // 512
  GUILD_MESSAGE_REACTIONS: 1 << 10,  // 1024
  GUILD_MESSAGE_TYPING: 1 << 11,     // 2048
  DIRECT_MESSAGES: 1 << 12,          // 4096
  DIRECT_MESSAGE_REACTIONS: 1 << 13, // 8192
  DIRECT_MESSAGE_TYPING: 1 << 14,    // 16384
  MESSAGE_CONTENT: 1 << 15,          // 32768 - privileged
  SCHEDULED_EVENTS: 1 << 16,         // 65536
  THREADS: 1 << 17,                  // 131072
  AUTOMOD: 1 << 18,                  // 262144
  AUDIT_LOG: 1 << 19,                // 524288
  ARTIFACTS: 1 << 20,                // 1048576
  SUMS: 1 << 21,                     // 2097152
};
```

### Aggregate values

| Set | Value | Description |
| --- | --- | --- |
| Default | `3178237` | All non-privileged intents, including `ARTIFACTS` and `SUMS` |
| All | `4194303` | Every intent (`(1 << 22) - 1`) |
| Privileged | `33026` | `GUILD_MEMBERS | GUILD_PRESENCES | MESSAGE_CONTENT` |

Derive the default programmatically instead of hard-coding it:

```javascript
const DEFAULT_INTENTS =
  Intents.GUILDS |
  Intents.GUILD_BANS |
  Intents.GUILD_EMOJIS |
  Intents.GUILD_INTEGRATIONS |
  Intents.GUILD_WEBHOOKS |
  Intents.GUILD_INVITES |
  Intents.GUILD_VOICE_STATES |
  Intents.GUILD_MESSAGES |
  Intents.GUILD_MESSAGE_REACTIONS |
  Intents.GUILD_MESSAGE_TYPING |
  Intents.DIRECT_MESSAGES |
  Intents.DIRECT_MESSAGE_REACTIONS |
  Intents.DIRECT_MESSAGE_TYPING |
  Intents.ARTIFACTS |
  Intents.SUMS; // 3178237
```

### Privileged intents

These intents require approval. If you request them without permission, the gateway closes the connection with close code `4014` (Disallowed Intents).

- `GUILD_MEMBERS` (`1 << 1`)
- `GUILD_PRESENCES` (`1 << 8`)
- `MESSAGE_CONTENT` (`1 << 15`)

The server also validates the intent type. A non-integer value, a boolean, a negative number, or a value greater than `4194303` fails validation with close code `4013` (Invalid Intents). See `plexichat/src/api/websocket/intents.py`.

Request only the intents you need to reduce event volume and processing cost.

## Events

All dispatch payloads use opcode `0` and include `t` (event name), `d` (event data), and `s` (sequence number).

### Message events

Live message events use the flat payload family (the `MessageResponse` shape). The author is described by top-level string fields (`author_id`, `author_username`, `author_avatar_url`) instead of a nested `author` object, and timestamps are integer milliseconds. The canonical scope key is `server_id`; the same value is also emitted as `guild_id` for compatibility. For direct messages, both keys are `null`.

#### MESSAGE_CREATE

```json
{
  "op": 0,
  "t": "MESSAGE_CREATE",
  "d": {
    "id": "123456789012345678",
    "channel_id": "987654321098765432",
    "author_id": "111222333444555666",
    "author_username": "johndoe",
    "author_avatar_url": null,
    "content": "Hello everyone!",
    "created_at": 1705314600000,
    "edited_at": null,
    "deleted": false,
    "reply_to_id": null,
    "mentions": ["111222333444555666"],
    "attachments": [
      {
        "id": "555666777888999000",
        "filename": "cat.png",
        "content_type": "image/png",
        "size": 12345,
        "url": "/api/v1/media/attachments/...",
        "hash": "sha256..."
      }
    ],
    "embeds": [],
    "pinned": false,
    "status": "sent",
    "delivery_count": 0,
    "read_count": 0,
    "read": false,
    "read_by": [],
    "reactions": [],
    "metadata": null,
    "poll": null,
    "poll_results": null,
    "server_id": "111222333444555666",
    "guild_id": "111222333444555666"
  },
  "s": 42
}
```

- `mentions` lists the user IDs that were mentioned. Use it for mention notifications.
- A moderation tombstone is a `MESSAGE_UPDATE` with `deleted: true` or `metadata.automod_removed: true` plus `metadata.automod_violation_id`. Render the tombstone from those flags, not from content.

#### MESSAGE_UPDATE

`MESSAGE_UPDATE` carries the full flat message object. Replace your cached copy entirely. Pin and unpin broadcasts use the same shape with `edited_at: null`.

```json
{
  "op": 0,
  "t": "MESSAGE_UPDATE",
  "d": {
    "id": "123456789012345678",
    "channel_id": "987654321098765432",
    "author_id": "111222333444555666",
    "author_username": "johndoe",
    "content": "Hello everyone! (edited)",
    "created_at": 1705314600000,
    "edited_at": 1705314900000,
    "deleted": false,
    "mentions": [],
    "pinned": true,
    "server_id": "111222333444555666",
    "guild_id": "111222333444555666"
  },
  "s": 43
}
```

#### MESSAGE_DELETE

A hard delete removes the row. The payload is an identifier-only tombstone. If AutoMod performed the delete, `automod: true` is included.

```json
{
  "op": 0,
  "t": "MESSAGE_DELETE",
  "d": {
    "id": "123456789012345678",
    "channel_id": "987654321098765432",
    "server_id": "111222333444555666",
    "guild_id": "111222333444555666"
  },
  "s": 44
}
```

```json
{
  "op": 0,
  "t": "MESSAGE_DELETE",
  "d": {
    "id": "123456789012345678",
    "channel_id": "987654321098765432",
    "server_id": "111222333444555666",
    "guild_id": "111222333444555666",
    "automod": true
  },
  "s": 45
}
```

#### MESSAGE_DELETE_BULK

```json
{
  "op": 0,
  "t": "MESSAGE_DELETE_BULK",
  "d": {
    "channel_id": "987654321098765432",
    "message_ids": ["123456789012345678", "123456789012345679"],
    "server_id": "111222333444555666",
    "guild_id": "111222333444555666"
  },
  "s": 46
}
```

All identifier fields on live message events are strings.

### Presence events

#### PRESENCE_UPDATE

```json
{
  "op": 0,
  "t": "PRESENCE_UPDATE",
  "d": {
    "user": { "id": "123456789012345678" },
    "status": "online",
    "activities": [{ "name": "Playing Plexichat", "type": 0 }],
    "client_status": { "desktop": "online" }
  },
  "s": 45
}
```

### Typing events

#### TYPING_START

```json
{
  "op": 0,
  "t": "TYPING_START",
  "d": {
    "channel_id": "123456789012345678",
    "guild_id": "987654321098765432",
    "user_id": "111222333444555666",
    "timestamp": 1705314600000,
    "member": {
      "user": { "id": "111222333444555666", "username": "johndoe" },
      "nick": "John",
      "roles": []
    }
  },
  "s": 46
}
```

The gateway also dispatches `TYPING_STOP` (op `41`) when a user stops typing.

### Channel events

#### CHANNEL_CREATE

```json
{
  "op": 0,
  "t": "CHANNEL_CREATE",
  "d": {
    "id": "123456789012345678",
    "type": 0,
    "guild_id": "987654321098765432",
    "name": "new-channel",
    "position": 5,
    "topic": null,
    "nsfw": false
  },
  "s": 47
}
```

#### CHANNEL_UPDATE

```json
{
  "op": 0,
  "t": "CHANNEL_UPDATE",
  "d": {
    "id": "123456789012345678",
    "name": "renamed-channel",
    "topic": "New topic"
  },
  "s": 48
}
```

### Guild (server) events

#### GUILD_CREATE

```json
{
  "op": 0,
  "t": "GUILD_CREATE",
  "d": {
    "id": "123456789012345678",
    "name": "My Server",
    "icon": "icon_hash",
    "owner_id": "987654321098765432",
    "roles": [],
    "emojis": [],
    "channels": [],
    "members": []
  },
  "s": 49
}
```

#### GUILD_MEMBER_ADD

```json
{
  "op": 0,
  "t": "GUILD_MEMBER_ADD",
  "d": {
    "guild_id": "123456789012345678",
    "user": { "id": "987654321098765432", "username": "newmember" },
    "joined_at": "2024-01-15T10:30:00.000Z",
    "roles": []
  },
  "s": 50
}
```

### Relationship events

#### RELATIONSHIP_ADD

```json
{
  "op": 0,
  "t": "RELATIONSHIP_ADD",
  "d": {
    "id": "123456789012345678",
    "type": 1,
    "user": { "id": "123456789012345678", "username": "frienduser" }
  },
  "s": 51
}
```

Types: `1` = Friend, `2` = Blocked, `3` = Incoming request, `4` = Outgoing request.

## Voice gateway

### 1. Join a voice channel

```javascript
ws.send(JSON.stringify({
  op: 4,
  d: {
    guild_id: 'server_id',
    channel_id: 'voice_channel_id',
    self_mute: false,
    self_deaf: false
  }
}));
```

### 2. Receive a voice server update

```json
{
  "op": 0,
  "t": "VOICE_SERVER_UPDATE",
  "d": {
    "token": "voice_token",
    "guild_id": "server_id",
    "endpoint": "voice.plexichat.com:443"
  }
}
```

### 3. Connect to the voice server

Use the token and endpoint to establish a WebRTC connection. See the [voice API](../api/voice.md).

## Connection resilience

### Handle disconnections

```javascript
ws.onclose = (event) => {
  if (sessionId && sequence > 0 && shouldResume(event.code)) {
    attemptResume();
  } else {
    setTimeout(connect, 5000);
  }
};

function attemptResume() {
  const resumeWs = new WebSocket('ws://localhost:8000/gateway');
  resumeWs.onopen = () => {
    resumeWs.send(JSON.stringify({
      op: 6,
      d: {
        token: 'your_session_token',
        session_id: sessionId,
        seq: sequence
      }
    }));
  };
}
```

### Exponential backoff

```javascript
let reconnectAttempts = 0;
const maxReconnectDelay = 30000;

function handleDisconnect(event) {
  reconnectAttempts++;
  const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), maxReconnectDelay);
  setTimeout(() => {
    if (shouldResume(event.code)) {
      resume();
    } else {
      connect();
    }
  }, delay);
}

function onOpen() {
  reconnectAttempts = 0;
}
```

### Resume or fresh connect

- **Resume (op `6`).** Use when you have a valid `session_id` and sequence number. The server replays missed events. This is faster than a fresh connect.
- **Fresh connect (op `2`).** Use when you have no session, the session expired, or the close code is not resumable. You must authenticate again.

```javascript
function shouldResume(closeCode) {
  const nonResumableCodes = [4004, 4010, 4011, 4012, 4013, 4014, 4015, 4016, 4017];
  return !nonResumableCodes.includes(closeCode)
      && sessionId !== null
      && sequence > 0;
}
```

## Connection state machine

```
DISCONNECTED -> CONNECTING -> CONNECTED -> RESUMING
      ^              |              |              |
      +--------------<-------------<--------------+
```

| State | Description |
| --- | --- |
| `DISCONNECTED` | No connection |
| `CONNECTING` | WebSocket handshake in progress |
| `CONNECTED` | Authenticated and receiving events |
| `RESUMING` | Attempting to resume an existing session |

```javascript
const ConnectionState = {
  DISCONNECTED: 'DISCONNECTED',
  CONNECTING: 'CONNECTING',
  CONNECTED: 'CONNECTED',
  RESUMING: 'RESUMING'
};

class GatewayConnection {
  constructor(token, intents) {
    this.token = token;
    this.intents = intents;
    this.state = ConnectionState.DISCONNECTED;
    this.ws = null;
    this.sequence = 0;
    this.sessionId = null;
  }

  connect() {
    this.state = ConnectionState.CONNECTING;
    this.ws = new WebSocket('ws://localhost:8000/gateway');
    this.ws.onopen = () => {
      this.state = ConnectionState.CONNECTED;
      this.identify();
    };
    this.ws.onclose = (event) => {
      this.state = ConnectionState.DISCONNECTED;
      this.handleDisconnect(event);
    };
  }

  resume() {
    this.state = ConnectionState.RESUMING;
    this.ws = new WebSocket('ws://localhost:8000/gateway');
    this.ws.onopen = () => {
      this.ws.send(JSON.stringify({
        op: 6,
        d: { token: this.token, session_id: this.sessionId, seq: this.sequence }
      }));
    };
  }
}
```

## Heartbeat management

```javascript
class GatewayConnection {
  constructor(token, intents) {
    this.heartbeatInterval = null;
    this.heartbeatIntervalMs = null;
    this.lastHeartbeatAck = Date.now();
    this.heartbeatTimeoutMs = 20000;
  }

  startHeartbeat(intervalMs) {
    this.heartbeatIntervalMs = intervalMs;
    this.heartbeatInterval = setInterval(() => this.sendHeartbeat(), intervalMs);
  }

  sendHeartbeat() {
    if (this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ op: 1, d: this.sequence }));
    }
  }

  handleHeartbeatAck() {
    this.lastHeartbeatAck = Date.now();
  }

  monitorHeartbeat() {
    setInterval(() => {
      if (Date.now() - this.lastHeartbeatAck > this.heartbeatTimeoutMs) {
        this.ws.close();
      }
    }, 5000);
  }
}
```

If you do not receive a heartbeat ACK within the timeout, close the connection and reconnect.

## Event dispatching architecture

```javascript
class EventDispatcher {
  constructor() {
    this.handlers = {};
  }

  on(eventType, handler) {
    (this.handlers[eventType] ??= []).push(handler);
  }

  emit(eventType, data) {
    for (const handler of this.handlers[eventType] ?? []) {
      try {
        handler(data);
      } catch (error) {
        console.error(`Error in ${eventType} handler:`, error);
      }
    }
  }

  off(eventType, handler) {
    const handlers = this.handlers[eventType] ?? [];
    const index = handlers.indexOf(handler);
    if (index > -1) handlers.splice(index, 1);
  }
}

class GatewayConnection {
  constructor(token, intents) {
    this.dispatcher = new EventDispatcher();
  }

  handleMessage(payload) {
    if (payload.s) this.sequence = payload.s;
    switch (payload.op) {
      case 0:
        this.dispatcher.emit(payload.t, payload.d);
        break;
      case 11:
        this.handleHeartbeatAck();
        break;
    }
  }

  on(eventType, handler) {
    this.dispatcher.on(eventType, handler);
  }
}
```

## Tab visibility handling

Suspend work when the tab is hidden to save resources.

```javascript
class GatewayConnection {
  setupVisibilityHandler() {
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        this.handleTabHidden();
      } else {
        this.handleTabVisible();
      }
    });
  }

  handleTabHidden() {
    if (this.heartbeatInterval) {
      clearInterval(this.heartbeatInterval);
      this.heartbeatInterval = null;
    }
  }

  handleTabVisible() {
    if (this.heartbeatIntervalMs && !this.heartbeatInterval) {
      this.startHeartbeat(this.heartbeatIntervalMs);
    }
    if (Date.now() - this.lastHeartbeatAck > this.heartbeatTimeoutMs) {
      this.ws.close();
    }
  }
}
```

## Certificate warning handling

For self-signed certificates, detect the failure and guide the user to accept the certificate.

```javascript
class GatewayConnection {
  connect(url) {
    this.ws = new WebSocket(url);
    this.ws.onerror = (error) => {
      if (this.isCertificateError(error)) this.handleCertificateError();
    };
  }

  isCertificateError(error) {
    return error.message && (
      error.message.includes('certificate') ||
      error.message.includes('SSL') ||
      error.message.includes('TLS')
    );
  }

  handleCertificateError() {
    alert(
      'This server uses a self-signed certificate. ' +
      'Open the server URL in a new tab, accept the certificate, ' +
      'then reload this page.'
    );
  }
}
```

## Best practices

### Queue messages during reconnection

```javascript
const messageQueue = [];

function sendToGateway(data) {
  if (ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(data));
  } else {
    messageQueue.push(data);
  }
}

ws.onopen = () => {
  while (messageQueue.length > 0) {
    ws.send(JSON.stringify(messageQueue.shift()));
  }
};
```

### Track sequence numbers

Update the sequence number on every payload that includes `s` so you can resume.

```javascript
ws.onmessage = (event) => {
  const payload = JSON.parse(event.data);
  if (payload.s) sequence = payload.s;
};
```

### Request only the intents you need

```javascript
// Avoid requesting everything
const bad = 0b11111111111111111111;

// Request only what you need
const good = Intents.GUILDS | Intents.GUILD_MESSAGES;
```

## Client examples

### React hook

```javascript
import { useEffect, useRef, useState } from 'react';

function useGateway(token) {
  const [connected, setConnected] = useState(false);
  const [messages, setMessages] = useState([]);
  const wsRef = useRef(null);

  useEffect(() => {
    const ws = new WebSocket('ws://localhost:8000/gateway');
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      ws.send(JSON.stringify({ op: 2, d: { token, intents: 513 } }));
    };

    ws.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      if (payload.t === 'MESSAGE_CREATE') {
        setMessages((prev) => [...prev, payload.d]);
      }
    };

    ws.onclose = () => setConnected(false);
    return () => ws.close();
  }, [token]);

  return { connected, messages };
}
```

### Node.js client

```javascript
const WebSocket = require('ws');

class PlexiGateway {
  constructor(token) {
    this.token = token;
    this.ws = null;
    this.seq = 0;
    this.sessionId = null;
    this.heartbeatInterval = null;
  }

  connect() {
    this.ws = new WebSocket('ws://localhost:8000/gateway');
    this.ws.on('open', () => console.log('Gateway connected'));
    this.ws.on('message', (data) => this.handleMessage(JSON.parse(data)));
    this.ws.on('close', () => clearInterval(this.heartbeatInterval));
  }

  handleMessage(payload) {
    if (payload.s) this.seq = payload.s;
    switch (payload.op) {
      case 10:
        this.identify();
        this.startHeartbeat(payload.d.heartbeat_interval);
        break;
      case 0:
        this.emit(payload.t, payload.d);
        break;
    }
  }

  identify() {
    this.ws.send(JSON.stringify({ op: 2, d: { token: this.token, intents: 513 } }));
  }

  startHeartbeat(interval) {
    this.heartbeatInterval = setInterval(() => {
      this.ws.send(JSON.stringify({ op: 1, d: this.seq }));
    }, interval);
  }

  emit(event, data) {
    console.log(`Event: ${event}`, data);
  }
}

const client = new PlexiGateway('your_token');
client.connect();
```

## Gateway close codes

Close codes are defined in `plexichat/src/api/websocket/opcodes.py`.

### Standard codes

| Code | Name | Description | Resumable |
| --- | --- | --- | --- |
| 1000 | Normal Closure | Clean disconnect | No |
| 1001 | Going Away | Server shutting down | No |

### Gateway-specific codes (4000–4017)

| Code | Name | Description | Resumable |
| --- | --- | --- | --- |
| 4000 | Unknown Error | Unexpected server error | Yes |
| 4001 | Unknown Opcode | Invalid opcode | Yes |
| 4002 | Decode Error | Invalid JSON or encoding | Yes |
| 4003 | Not Authenticated | Sent message before identify | Yes |
| 4004 | Authentication Failed | Invalid token | No |
| 4005 | Already Authenticated | Already sent Identify | Yes |
| 4007 | Invalid Seq | Invalid sequence number | Yes |
| 4008 | Rate Limited | Too many messages | Yes |
| 4009 | Session Timed Out | Missed heartbeats | Yes |
| 4010 | Invalid Shard | Invalid sharding info | No |
| 4011 | Sharding Required | Server requires sharding | No |
| 4012 | Invalid API Version | Unsupported API version | No |
| 4013 | Invalid Intents | Invalid intent value | No |
| 4014 | Disallowed Intents | Privileged intent not allowed | No |
| 4015 | Version Outdated | Client version too old | No |
| 4016 | Server Maintenance | Server entering maintenance | No |
| 4017 | Server Shutdown | Server is shutting down | No |

You can resume (op `6`) after `4000`, `4001`, `4002`, `4003`, `4005`, `4007`, `4008`, and `4009`. For other codes, reconnect with a fresh Identify.

## Rate limits

| Payload | Limit |
| --- | --- |
| Identify (op `2`) | 1 per 5 seconds per token |
| Resume (op `6`) | No limit |
| Other payloads | 120 per 60 seconds |

If you exceed these limits, the gateway closes the connection with code `4008`.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Connection refused | Server not running or firewall blocks the port | Verify the host and port, check firewall rules, and confirm WebSockets are enabled |
| Close code `4004` | Invalid or expired token | Verify the token format and expiry, and complete 2FA if required |
| Close code `4013` | Invalid intent value | Send an integer bitmask at or below `4194303`; do not send arrays, strings, or booleans |

## Related docs

- [API reference](../api/index.md)
- [Voice API](../api/voice.md)
- [Rate limits](../rate-limits.md)
- [Gateway opcodes](../websocket/opcodes.md)
- [Gateway intents](../websocket/intents.md)
