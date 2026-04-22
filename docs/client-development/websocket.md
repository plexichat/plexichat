# WebSocket Gateway Guide

The Plexichat WebSocket Gateway provides real-time event streaming for clients, enabling instant updates for messages, presence, typing indicators, and more.

## Gateway URL

### Production

```
wss://api.plexichat.com/gateway
```

### Development

```
ws://localhost:8000/gateway
```

The gateway endpoint is mounted at `/gateway` on the API server. For the Plexichat web client, use `app.plexichat.com` for the frontend and `api.plexichat.com` for API/gateway connections.

## Connection Flow

### 1. Establish Connection

Connect to the WebSocket endpoint and authenticate:

```javascript
const ws = new WebSocket('wss://api.plexichat.com/gateway');

ws.onopen = () => {
  console.log('Connected to gateway');
  
  // Authenticate with your session token
  ws.send(JSON.stringify({
    op: 2,  // Identify
    d: {
      token: 'your_session_token_here',
      properties: {
        os: 'windows',
        browser: 'chrome',
        device: 'desktop'
      },
      intents: 4607  // Default non-privileged intents (GUILDS | GUILD_MESSAGES | DIRECT_MESSAGES)
    }
  }));
};
```

### 2. Handle Events

```javascript
ws.onmessage = (event) => {
  const payload = JSON.parse(event.data);
  
  switch (payload.op) {
    case 0:  // Dispatch (events)
      handleDispatch(payload.t, payload.d);
      break;
    case 1:  // Heartbeat
      sendHeartbeat();
      break;
    case 10: // Hello (connection established)
      startHeartbeat(payload.d.heartbeat_interval);
      break;
    case 11: // Heartbeat ACK
      lastHeartbeatAck = Date.now();
      break;
    case 9:  // Invalid session
      handleInvalidSession();
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
    // ... handle other events
  }
}
```

### 3. Heartbeat

Maintain connection with regular heartbeats:

```javascript
let heartbeatInterval;
let sequence = 0;

function startHeartbeat(interval) {
  heartbeatInterval = setInterval(() => {
    ws.send(JSON.stringify({
      op: 1,  // Heartbeat
      d: sequence
    }));
  }, interval);
}

function sendHeartbeat() {
  ws.send(JSON.stringify({
    op: 1,
    d: sequence
  }));
}
```

## Gateway Opcodes

| Opcode | Name | Description | Sent By |
|--------|------|-------------|---------|
| 0 | Dispatch | Event dispatch | Server |
| 1 | Heartbeat | Ping for connection keep-alive | Client |
| 2 | Identify | Authenticate and start session | Client |
| 3 | Presence Update | Update user presence | Client |
| 4 | Voice State Update | Join/leave voice channel | Client |
| 6 | Resume | Resume dropped connection | Client |
| 7 | Reconnect | Server requests client reconnect | Server |
| 8 | Request Guild Members | Request member list | Client |
| 9 | Invalid Session | Session invalidated | Server |
| 10 | Hello | Initial connection handshake | Server |
| 11 | Heartbeat ACK | Heartbeat acknowledgment | Server |
| 12 | Server Status | Server status update | Server |
| 13 | Version Check | Client version check | Client |
| 20 | Voice Connect | Initiate voice connection | Client |
| 21 | Voice Disconnect | End voice connection | Client |
| 22 | Voice SDP Offer | WebRTC offer | Client/Server |
| 23 | Voice SDP Answer | WebRTC answer | Client/Server |
| 24 | Voice ICE Candidate | ICE candidate exchange | Client/Server |
| 25 | Voice Speaking | Speaking state update | Client/Server |
| 26 | Voice Quality | Quality metrics | Server |
| 30 | Interaction Create | Application interaction | Server |
| 31 | Interaction Response | Interaction response | Client |
| 40 | Typing Start | User started typing | Server |
| 41 | Typing Stop | User stopped typing | Server |

## Intents

Intents determine which events your client receives. Based on the actual source code (`src/core/events/types.py`), here are the intent values:

```javascript
const Intents = {
  GUILDS: 1 << 0,                    // 1 - Guild events (create, update, delete, channels, roles)
  GUILD_MEMBERS: 1 << 1,             // 2 - Guild member events (privileged)
  GUILD_BANS: 1 << 2,                // 4 - Guild ban events
  GUILD_EMOJIS: 1 << 3,              // 8 - Guild emoji events
  GUILD_INTEGRATIONS: 1 << 4,        // 16 - Guild integration events
  GUILD_WEBHOOKS: 1 << 5,            // 32 - Guild webhook events
  GUILD_INVITES: 1 << 6,             // 64 - Guild invite events
  GUILD_VOICE_STATES: 1 << 7,        // 128 - Voice state events
  GUILD_PRESENCES: 1 << 8,           // 256 - Presence events (privileged)
  GUILD_MESSAGES: 1 << 9,            // 512 - Guild message events
  GUILD_MESSAGE_REACTIONS: 1 << 10,  // 1024 - Guild reaction events
  GUILD_MESSAGE_TYPING: 1 << 11,     // 2048 - Guild typing events
  DIRECT_MESSAGES: 1 << 12,          // 4096 - Direct message events
  DIRECT_MESSAGE_REACTIONS: 1 << 13, // 8192 - DM reaction events
  DIRECT_MESSAGE_TYPING: 1 << 14,    // 16384 - DM typing events
  MESSAGE_CONTENT: 1 << 15,           // 32768 - Message content (privileged)
  SCHEDULED_EVENTS: 1 << 16,         // 65536 - Scheduled events
  THREADS: 1 << 17,                   // 131072 - Thread events
  AUTOMOD: 1 << 18,                   // 262144 - AutoMod events
  AUDIT_LOG: 1 << 19,                 // 524288 - Audit log events
};
```

### Default Intents

The server uses these default intents (non-privileged):

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
  Intents.DIRECT_MESSAGE_TYPING;  // = 4607
```

### Privileged Intents

These intents require special configuration:

- `GUILD_MEMBERS` (2) - Member list and join events
- `GUILD_PRESENCES` (256) - Presence and status updates  
- `MESSAGE_CONTENT` (32768) - Message content in guilds

Privileged intents must be enabled in the server configuration. Requests for these intents without permission will result in a close code 4014 (Disallowed Intents).

## Events

### Message Events

#### MESSAGE_CREATE

```json
{
  "op": 0,
  "t": "MESSAGE_CREATE",
  "d": {
    "id": "123456789012345678",
    "channel_id": "987654321098765432",
    "author": {
      "id": "111222333444555666",
      "username": "johndoe",
      "avatar": "avatar_hash"
    },
    "content": "Hello everyone!",
    "timestamp": "2024-01-15T10:30:00.000Z",
    "edited_timestamp": null,
    "mentions": [],
    "attachments": [],
    "embeds": [],
    "reactions": []
  },
  "s": 42
}
```

#### MESSAGE_UPDATE

```json
{
  "op": 0,
  "t": "MESSAGE_UPDATE",
  "d": {
    "id": "123456789012345678",
    "channel_id": "987654321098765432",
    "content": "Hello everyone! (edited)",
    "edited_timestamp": "2024-01-15T10:35:00.000Z"
  },
  "s": 43
}
```

#### MESSAGE_DELETE

```json
{
  "op": 0,
  "t": "MESSAGE_DELETE",
  "d": {
    "id": "123456789012345678",
    "channel_id": "987654321098765432",
    "guild_id": "111222333444555666"
  },
  "s": 44
}
```

### Presence Events

#### PRESENCE_UPDATE

```json
{
  "op": 0,
  "t": "PRESENCE_UPDATE",
  "d": {
    "user": {
      "id": "123456789012345678"
    },
    "status": "online",
    "activities": [
      {
        "name": "Playing Plexichat",
        "type": 0
      }
    ],
    "client_status": {
      "desktop": "online"
    }
  },
  "s": 45
}
```

### Typing Events

#### TYPING_START

```json
{
  "op": 0,
  "t": "TYPING_START",
  "d": {
    "channel_id": "123456789012345678",
    "guild_id": "987654321098765432",
    "user_id": "111222333444555666",
    "timestamp": 1705314600,
    "member": {
      "user": {
        "id": "111222333444555666",
        "username": "johndoe"
      },
      "nick": "John",
      "roles": []
    }
  },
  "s": 46
}
```

### Channel Events

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

### Guild (Server) Events

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
    "user": {
      "id": "987654321098765432",
      "username": "newmember"
    },
    "joined_at": "2024-01-15T10:30:00.000Z",
    "roles": []
  },
  "s": 50
}
```

### Relationship Events

#### RELATIONSHIP_ADD

```json
{
  "op": 0,
  "t": "RELATIONSHIP_ADD",
  "d": {
    "id": "123456789012345678",
    "type": 1,
    "user": {
      "id": "123456789012345678",
      "username": "frienduser"
    }
  },
  "s": 51
}
```

Types: `1` = Friend, `2` = Blocked, `3` = Incoming Request, `4` = Outgoing Request

## Voice Gateway

Voice connections use a separate flow:

### 1. Join Voice Channel

```javascript
ws.send(JSON.stringify({
  op: 4,  // Voice State Update
  d: {
    guild_id: 'server_id',
    channel_id: 'voice_channel_id',
    self_mute: false,
    self_deaf: false
  }
}));
```

### 2. Receive Voice Server Update

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

### 3. Connect to Voice Server

Use the provided token and endpoint to establish WebRTC connection. See [Voice API](../api/voice.md) for details.

## Connection Resilience

### Handling Disconnections

```javascript
ws.onclose = (event) => {
  console.log('Disconnected:', event.code, event.reason);
  
  // Attempt to resume if we have a session_id
  if (sessionId && sequence > 0) {
    attemptResume();
  } else {
    // Reconnect from scratch
    setTimeout(connect, 5000);
  }
};

function attemptResume() {
  const resumeWs = new WebSocket('ws://localhost:8000/gateway');
  
  resumeWs.onopen = () => {
    resumeWs.send(JSON.stringify({
      op: 6,  // Resume
      d: {
        token: 'your_session_token',
        session_id: sessionId,
        seq: sequence
      }
    }));
  };
}
```

### Exponential Backoff

```javascript
let reconnectAttempts = 0;
const maxReconnectDelay = 30000; // 30 seconds

function connect() {
  const ws = new WebSocket('wss://api.plexichat.com/gateway');
  
  ws.onclose = () => {
    reconnectAttempts++;
    const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), maxReconnectDelay);
    
    console.log(`Reconnecting in ${delay}ms...`);
    setTimeout(connect, delay);
  };
  
  ws.onopen = () => {
    reconnectAttempts = 0; // Reset on successful connection
  };
}
```

## Best Practices

### 1. Message Queuing

Queue messages during reconnection:

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
  // Flush queue
  while (messageQueue.length > 0) {
    ws.send(JSON.stringify(messageQueue.shift()));
  }
};
```

### 2. Heartbeat Monitoring

Track heartbeat acknowledgments:

```javascript
let lastHeartbeatAck = Date.now();
const heartbeatTimeout = 20000; // 20 seconds

setInterval(() => {
  if (Date.now() - lastHeartbeatAck > heartbeatTimeout) {
    console.error('Heartbeat timeout - reconnecting');
    ws.close();
  }
}, 5000);
```

### 3. Sequence Number Tracking

Always track the sequence number for resume capability:

```javascript
ws.onmessage = (event) => {
  const payload = JSON.parse(event.data);
  
  if (payload.s) {
    sequence = payload.s; // Update sequence number
  }
  
  // ... handle payload
};
```

### 4. Efficient Intent Usage

Only request intents you need:

```javascript
// Bad: Request everything
const intents = 0b11111111111111111111;

// Good: Request only what you need
const intents = Intents.GUILDS | Intents.GUILD_MESSAGES;
```

## Client Examples

### React Hook Example

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
      ws.send(JSON.stringify({
        op: 2,
        d: { token, intents: 513 }
      }));
    };
    
    ws.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      
      if (payload.t === 'MESSAGE_CREATE') {
        setMessages(prev => [...prev, payload.d]);
      }
    };
    
    ws.onclose = () => setConnected(false);
    
    return () => ws.close();
  }, [token]);
  
  return { connected, messages };
}
```

### Node.js Client

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
    
    this.ws.on('open', () => {
      console.log('Gateway connected');
    });
    
    this.ws.on('message', (data) => {
      this.handleMessage(JSON.parse(data));
    });
    
    this.ws.on('close', () => {
      console.log('Gateway disconnected');
      clearInterval(this.heartbeatInterval);
    });
  }
  
  handleMessage(payload) {
    if (payload.s) this.seq = payload.s;
    
    switch (payload.op) {
      case 10: // Hello
        this.identify();
        this.startHeartbeat(payload.d.heartbeat_interval);
        break;
      case 0: // Dispatch
        this.emit(payload.t, payload.d);
        break;
    }
  }
  
  identify() {
    this.ws.send(JSON.stringify({
      op: 2,
      d: {
        token: this.token,
        intents: 513
      }
    }));
  }
  
  startHeartbeat(interval) {
    this.heartbeatInterval = setInterval(() => {
      this.ws.send(JSON.stringify({
        op: 1,
        d: this.seq
      }));
    }, interval);
  }
  
  emit(event, data) {
    console.log(`Event: ${event}`, data);
  }
}

const client = new PlexiGateway('your_token');
client.connect();
```

## Gateway Close Codes

When the gateway closes the connection, it uses these standard WebSocket close codes (defined in `src/api/websocket/opcodes.py`):

### Standard Codes

| Code | Name | Description | Resumable |
|------|------|-------------|-----------|
| 1000 | Normal Closure | Clean disconnect | No |
| 1001 | Going Away | Server shutting down | No |

### Gateway-Specific Codes (4000-4017)

| Code | Name | Description | Resumable |
|------|------|-------------|-----------|
| 4000 | Unknown Error | Unexpected server error | Yes |
| 4001 | Unknown Opcode | Invalid opcode sent | Yes |
| 4002 | Decode Error | Invalid JSON or encoding | Yes |
| 4003 | Not Authenticated | Sent message before IDENTIFY | Yes |
| 4004 | Authentication Failed | Invalid token | No |
| 4005 | Already Authenticated | Already sent IDENTIFY | Yes |
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

### Resumable Sessions

A session can be resumed (using `op: 6 Resume`) after these close codes:
- 4000, 4001, 4002, 4003, 4005, 4007, 4008, 4009

After other codes (especially authentication-related), you must reconnect with a fresh IDENTIFY.

## Rate Limits

Gateway connections are rate limited:

- **Identify**: 1 per 5 seconds per token
- **Resume**: No limit
- **Other payloads**: 120 per 60 seconds

Exceeding limits results in connection termination with code 4008.

## Troubleshooting

### Connection Refused

- Verify server is running on correct port
- Check firewall rules
- Ensure WebSocket is enabled in server config

### Authentication Failed (4004)

- Verify token is valid and not expired
- Check token format (should be session token, not bot token format)
- Ensure 2FA is completed if required

### Invalid Intents (4013)

- Verify intent calculation is correct
- Check if privileged intents are enabled
- Ensure intents are passed as integer, not array

## Related Documentation

- [API Reference](../api/index.md) - REST API documentation
- [Voice Routes](../api/voice.md) - Voice channel WebRTC signaling
- [Rate Limits](../client-development/rate-limits.md) - API and gateway rate limiting
