# Building a Custom Client

This guide covers building a custom Plexichat client from scratch, including authentication, state management, WebSocket integration, and API usage.

## Overview

A Plexichat client requires:
- REST API integration for authentication and data
- WebSocket gateway connection for real-time events
- Local state management and caching
- Message encryption support
- UI rendering for messages, servers, and channels

## Authentication Flow

### Registration

```http
POST /api/v1/auth/register
Content-Type: application/json

{
  "username": "johndoe",
  "email": "john@example.com",
  "password": "securepassword123"
}
```

Response:
```json
{
  "user": {
    "id": "123456789012345678",
    "username": "johndoe",
    "email": "john@example.com"
  },
  "token": "session_token_here"
}
```

### Login

```http
POST /api/v1/auth/login
Content-Type: application/json

{
  "email": "john@example.com",
  "password": "securepassword123"
}
```

Response:
```json
{
  "user": {
    "id": "123456789012345678",
    "username": "johndoe",
    "email": "john@example.com"
  },
  "token": "session_token_here"
}
```

### Two-Factor Authentication

If 2FA is enabled, login returns a 401 with requires_2fa flag:

```json
{
  "error": "requires_2fa",
  "message": "Two-factor authentication required"
}
```

Submit 2FA code:

```http
POST /api/v1/auth/2fa/verify
Content-Type: application/json
Authorization: Bearer session_token

{
  "code": "123456"
}
```

### Session Token Usage

Include the session token in the Authorization header for all authenticated requests:

```
Authorization: Bearer session_token_here
```

## API Client Implementation

### Basic API Client

```javascript
class PlexiAPI {
  constructor(baseUrl, token) {
    this.baseUrl = baseUrl;
    this.token = token;
  }

  async request(method, endpoint, data = null) {
    const url = `${this.baseUrl}${endpoint}`;
    const headers = {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${this.token}`
    };

    const options = {
      method,
      headers
    };

    if (data) {
      options.body = JSON.stringify(data);
    }

    const response = await fetch(url, options);

    if (!response.ok) {
      throw new Error(`API error: ${response.status}`);
    }

    return response.json();
  }

  async getUser() {
    return this.request('GET', '/users/@me');
  }

  async getServers() {
    return this.request('GET', '/servers');
  }

  async getMessages(channelId, limit = 50) {
    return this.request('GET', `/channels/${channelId}/messages?limit=${limit}`);
  }

  async sendMessage(channelId, content) {
    return this.request('POST', `/channels/${channelId}/messages`, { content });
  }
}
```

### Error Handling

API errors return JSON with error details:

```json
{
  "error": "invalid_token",
  "message": "Invalid or expired token"
}
```

Handle 401 errors by prompting re-authentication. Handle 429 errors with retry-after delay.

## WebSocket Gateway Integration

### Connection

```javascript
class PlexiGateway {
  constructor(token, intents) {
    this.token = token;
    this.intents = intents;
    this.ws = null;
    this.sequence = 0;
    this.sessionId = null;
    this.heartbeatInterval = null;
  }

  connect() {
    this.ws = new WebSocket('ws://localhost:8000/gateway');

    this.ws.onopen = () => {
      console.log('Gateway connected');
    };

    this.ws.onmessage = (event) => {
      this.handleMessage(JSON.parse(event.data));
    };

    this.ws.onclose = (event) => {
      console.log('Gateway disconnected:', event.code);
      this.handleReconnect();
    };
  }

  handleMessage(payload) {
    if (payload.s) this.sequence = payload.s;

    switch (payload.op) {
      case 10: // Hello
        this.identify();
        this.startHeartbeat(payload.d.heartbeat_interval);
        break;
      case 0: // Dispatch
        this.handleEvent(payload.t, payload.d);
        break;
      case 11: // Heartbeat ACK
        this.lastHeartbeatAck = Date.now();
        break;
    }
  }

  identify() {
    this.ws.send(JSON.stringify({
      op: 2,
      d: {
        token: this.token,
        properties: {
          os: 'windows',
          browser: 'custom-client',
          device: 'desktop'
        },
        intents: this.intents
      }
    }));
  }

  startHeartbeat(interval) {
    this.heartbeatInterval = setInterval(() => {
      this.ws.send(JSON.stringify({
        op: 1,
        d: this.sequence
      }));
    }, interval);
  }

  handleEvent(eventType, data) {
    switch (eventType) {
      case 'MESSAGE_CREATE':
        this.onMessageCreate(data);
        break;
      case 'PRESENCE_UPDATE':
        this.onPresenceUpdate(data);
        break;
      // Handle other events
    }
  }

  handleReconnect() {
    if (this.sessionId) {
      // Resume
      this.ws = new WebSocket('ws://localhost:8000/gateway');
      this.ws.onopen = () => {
        this.ws.send(JSON.stringify({
          op: 6,
          d: {
            token: this.token,
            session_id: this.sessionId,
            seq: this.sequence
          }
        }));
      };
    } else {
      // Fresh connect
      setTimeout(() => this.connect(), 5000);
    }
  }
}
```

### Intents

Calculate intents based on events you need:

```javascript
const Intents = {
  GUILDS: 1 << 0,
  GUILD_MEMBERS: 1 << 1,
  GUILD_BANS: 1 << 2,
  GUILD_EMOJIS: 1 << 3,
  GUILD_INTEGRATIONS: 1 << 4,
  GUILD_WEBHOOKS: 1 << 5,
  GUILD_INVITES: 1 << 6,
  GUILD_VOICE_STATES: 1 << 7,
  GUILD_PRESENCES: 1 << 8,
  GUILD_MESSAGES: 1 << 9,
  GUILD_MESSAGE_REACTIONS: 1 << 10,
  GUILD_MESSAGE_TYPING: 1 << 11,
  DIRECT_MESSAGES: 1 << 12,
  DIRECT_MESSAGE_REACTIONS: 1 << 13,
  DIRECT_MESSAGE_TYPING: 1 << 14,
  MESSAGE_CONTENT: 1 << 15,
  SCHEDULED_EVENTS: 1 << 16,
  THREADS: 1 << 17,
  AUTOMOD: 1 << 18,
  AUDIT_LOG: 1 << 19
};

// Example: Guilds + Messages
const intents = Intents.GUILDS | Intents.GUILD_MESSAGES;
```

## State Management

### Basic State Structure

```javascript
const AppState = {
  currentUser: null,
  servers: [],
  channels: {},
  messages: {},
  members: {},
  roles: {},
  relationships: [],
  currentServer: null,
  currentChannel: null
};
```

### IndexedDB Caching

IndexedDB provides offline caching for instant UI rendering.

#### Schema

```javascript
const DB_NAME = 'PlexichatCache';
const DB_VERSION = 2;

async function initDB() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);

    request.onerror = () => reject(request.error);
    request.onsuccess = () => resolve(request.result);

    request.onupgradeneeded = (event) => {
      const db = event.target.result;

      // Messages store
      if (!db.objectStoreNames.contains('messages')) {
        const msgStore = db.createObjectStore('messages', { keyPath: 'id' });
        msgStore.createIndex('channel_id', 'channel_id', { unique: false });
        msgStore.createIndex('created_at', 'created_at', { unique: false });
      }

      // Servers store
      if (!db.objectStoreNames.contains('servers')) {
        db.createObjectStore('servers', { keyPath: 'id' });
      }

      // Channels store
      if (!db.objectStoreNames.contains('channels')) {
        const chStore = db.createObjectStore('channels', { keyPath: 'id' });
        chStore.createIndex('server_id', 'server_id', { unique: false });
      }

      // Users store
      if (!db.objectStoreNames.contains('users')) {
        db.createObjectStore('users', { keyPath: 'id' });
      }
    };
  });
}
```

#### Caching Messages

```javascript
async function cacheMessages(channelId, messages) {
  const tx = db.transaction('messages', 'readwrite');
  const store = tx.objectStore('messages');

  for (const msg of messages) {
    store.put({ ...msg, channel_id: channelId });
  }
}
```

#### Loading Cached Messages

```javascript
async function getCachedMessages(channelId, limit = 50) {
  const tx = db.transaction('messages', 'readonly');
  const store = tx.objectStore('messages');
  const index = store.index('channel_id');
  const request = index.getAll(channelId);

  return new Promise((resolve) => {
    request.onsuccess = () => {
      const messages = request.result || [];
      messages.sort((a, b) => a.created_at - b.created_at);
      resolve(messages.slice(-limit));
    };
  });
}
```

### Cache Invalidation

- Clear channel messages when new messages arrive via WebSocket
- Refresh server list on GUILD_CREATE/GUILD_DELETE events
- Update user data on USER_UPDATE events
- Version cache to handle schema changes

## Message Encryption

Plexichat supports end-to-end message encryption. Messages are encrypted on the server and decrypted by the client.

### Crypto Initialization

```javascript
class PlexiCrypto {
  constructor(userId, token) {
    this.userId = userId;
    this.token = token;
    this.key = null;
  }

  async init() {
    // Derive encryption key from user ID and token
    const keyMaterial = await this.importKey(this.userId + this.token);
    this.key = await this.deriveKey(keyMaterial);
  }

  async importKey(password) {
    const enc = new TextEncoder();
    return window.crypto.subtle.importKey(
      'raw',
      enc.encode(password),
      'PBKDF2',
      false,
      ['deriveKey']
    );
  }

  async deriveKey(keyMaterial) {
    const salt = new TextEncoder().encode('plexichat-salt');
    return window.crypto.subtle.deriveKey(
      {
        name: 'PBKDF2',
        salt: salt,
        iterations: 100000,
        hash: 'SHA-256'
      },
      keyMaterial,
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt']
    );
  }

  async decrypt(encryptedContent) {
    if (!this.key) return encryptedContent;

    try {
      const data = JSON.parse(encryptedContent);
      const iv = this.base64ToArrayBuffer(data.iv);
      const ciphertext = this.base64ToArrayBuffer(data.ciphertext);

      const decrypted = await window.crypto.subtle.decrypt(
        { name: 'AES-GCM', iv: iv },
        this.key,
        ciphertext
      );

      const dec = new TextDecoder();
      return dec.decode(decrypted);
    } catch (e) {
      console.error('Decryption failed:', e);
      return encryptedContent;
    }
  }

  base64ToArrayBuffer(base64) {
    const binaryString = atob(base64);
    const bytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) {
      bytes[i] = binaryString.charCodeAt(i);
    }
    return bytes.buffer;
  }
}
```

### Usage

```javascript
const crypto = new PlexiCrypto(userId, token);
await crypto.init();

// Decrypt message content
const decryptedContent = await crypto.decrypt(message.content);
```

## Theme System

Plexichat uses CSS custom properties for theming. Themes are applied via data attributes.

### Color Themes

```css
[data-theme="dark"] {
  --bg-primary: #36393f;
  --bg-secondary: #2f3136;
  --bg-tertiary: #202225;
  --text-primary: #dcddde;
  --text-secondary: #96989d;
  --accent: #5865f2;
}

[data-theme="light"] {
  --bg-primary: #ffffff;
  --bg-secondary: #f2f3f5;
  --bg-tertiary: #e3e5e8;
  --text-primary: #060607;
  --text-secondary: #4e5058;
  --accent: #5865f2;
}
```

### Layout Modes

Two layout modes are available:

**Classic**: Gaming-focused, compact layout with vertical server sidebar
**Next**: Business-oriented layout with horizontal server bar

```css
body.theme-classic {
  --server-sidebar-width: 72px;
  --channel-sidebar-width: 240px;
  --members-sidebar-width: 240px;
}

body.theme-next {
  --next-topbar-height: 56px;
  --next-sidebar-width: 260px;
  --next-members-width: 240px;
}
```

### Applying Themes

```javascript
function setTheme(theme) {
  document.body.setAttribute('data-theme', theme);
  localStorage.setItem('plexichat-theme', theme);
}

function setLayoutMode(mode) {
  document.body.classList.remove('theme-classic', 'theme-next');
  document.body.classList.add(`theme-${mode}`);
  localStorage.setItem('plexichat-layout', mode);
}
```

## Data Types

### Snowflake IDs

Plexichat uses snowflake IDs for all entities. These are 64-bit integers that encode timestamps.

```javascript
function extractTimestamp(snowflake) {
  return (snowflake / 4194304) + 1420070400000;
}

const timestamp = extractTimestamp(messageId);
const date = new Date(timestamp);
```

### Timestamps

All timestamps are Unix timestamps in milliseconds.

```javascript
const date = new Date(message.created_at);
```

## Rate Limiting

API endpoints are rate limited. Handle 429 responses:

```json
{
  "error": "rate_limited",
  "message": "Too many requests",
  "retry_after": 5
}
```

Implement exponential backoff:

```javascript
async function requestWithRetry(fn, maxRetries = 3) {
  for (let i = 0; i < maxRetries; i++) {
    try {
      return await fn();
    } catch (error) {
      if (error.status === 429) {
        const retryAfter = error.retry_after * 1000;
        await new Promise(resolve => setTimeout(resolve, retryAfter));
      } else {
        throw error;
      }
    }
  }
}
```

## Complete Example

```javascript
class PlexiClient {
  constructor(baseUrl) {
    this.api = new PlexiAPI(baseUrl);
    this.gateway = null;
    this.state = AppState;
    this.crypto = null;
  }

  async login(email, password) {
    const response = await this.api.request('POST', '/auth/login', {
      email,
      password
    });

    this.api.token = response.token;
    this.state.currentUser = response.user;

    // Initialize crypto
    this.crypto = new PlexiCrypto(response.user.id, response.token);
    await this.crypto.init();

    // Connect gateway
    this.gateway = new PlexiGateway(response.token, 513);
    this.gateway.connect();

    return response;
  }

  async loadServers() {
    const servers = await this.api.getServers();
    this.state.servers = servers;
    return servers;
  }

  async loadMessages(channelId) {
    // Try cache first
    const cached = await getCachedMessages(channelId);
    if (cached.length > 0) {
      this.state.messages[channelId] = cached;
    }

    // Fetch from API
    const messages = await this.api.getMessages(channelId);
    this.state.messages[channelId] = messages;

    // Update cache
    await cacheMessages(channelId, messages);

    return messages;
  }

  async sendMessage(channelId, content) {
    const message = await this.api.sendMessage(channelId, content);

    // Add to local state
    if (!this.state.messages[channelId]) {
      this.state.messages[channelId] = [];
    }
    this.state.messages[channelId].push(message);

    return message;
  }
}
```

## API Reference

For complete API documentation, refer to the server-side OpenAPI docs at `/docs` or `/redoc`. The authoritative schemas are defined in `src/api/schemas/` on the server.

Key endpoints:
- Authentication: `/api/v1/auth/*`
- Users: `/api/v1/users/*`
- Servers: `/api/v1/servers/*`
- Channels: `/api/v1/channels/*`
- Messages: `/api/v1/channels/{id}/messages`
- Voice: `/api/v1/voice/*`
- Relationships: `/api/v1/relationships/*`

## Testing

Test your client against a local Plexichat server:

```bash
# Start server
cd plexichat
python main.py

# Your client connects to ws://localhost:8000/gateway
# API base URL: http://localhost:8000/api/v1
```
