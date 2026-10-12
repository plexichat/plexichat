# Bot development

Build automated accounts that interact with Plexichat through the REST API and the WebSocket gateway.

## Overview

A bot is an automated user account that can:

- Send and receive messages.
- Manage servers and channels.
- Handle interactions and commands.
- Automate moderation tasks.
- Integrate with external services.

The gateway is at `/gateway` (`src/api/websocket/gateway.py:261`). In development, connect to `ws://localhost:8000/gateway`. In production, use `wss://your-host/gateway`.

All REST endpoints in this guide are prefixed with `/api/v1` unless noted. The API prefix defaults to `/api/v1` (`src/config_defaults.py:131`).

Errors use the standard envelope (`src/api/schemas/common.py:58`):

```json
{
  "error": {
    "code": 400,
    "message": "Bot already exists for this application"
  }
}
```

## Create a bot application

### Step 1: Create the application

Create an application to represent your bot:

```http
POST /api/v1/applications
Authorization: Bearer YOUR_USER_TOKEN
Content-Type: application/json

{
  "name": "My Awesome Bot",
  "description": "A bot that does awesome things",
  "bot_public": true,
  "bot_require_code_grant": false
}
```

Response `200 OK` (`ApplicationResponse`):

```json
{
  "id": "123456789012345678",
  "owner_id": "111222333444555666",
  "name": "My Awesome Bot",
  "description": "A bot that does awesome things",
  "icon_emoji": null,
  "icon_url": null,
  "bot_id": null,
  "bot": false,
  "bot_public": true,
  "capabilities": null,
  "created_at": 1704067200
}
```

Notes:

- No `client_secret` is returned on create. Manage secrets with `POST /api/v1/applications/{application_id}/regenerate-secret`, which returns `{"secret": "..."}`.
- `redirect_uris` are request-only fields on create and update; the server does not echo them in the response.
- No bot account is created automatically. `bot_id` is `null` and `bot` is `false` until you create the bot in the next step.

### Step 2: Create the bot account

Create the bot user for the application. On success, `bot_id` is set to the bot's user ID. Creating a second bot for the same application returns `400` with `{"error":{"code":400,"message":"A bot already exists for this application"}}`.

### Step 3: Generate a bot token

```http
POST /api/v1/applications/{application_id}/bot
Authorization: Bearer YOUR_USER_TOKEN
```

Response:

```json
{
  "token": "bot_token_here",
  "bot_id": "987654321098765432"
}
```

Store the token securely. It grants full access to the bot account.

## Authenticate a bot

Send the bot token with the `Bot` prefix:

```http
Authorization: Bot bot_token_here
```

User sessions use `Bearer <token>`; bots use `Bot <token>`.

## Choose a delivery method

### Use the WebSocket gateway when you need real-time events

Choose the gateway when you need low latency, full event access, or the ability to send messages at any time.

**Pros**

- Real-time dispatch of all subscribed events.
- Low latency.
- Bidirectional communication.

**Cons**

- Requires a persistent connection.
- More complex state management.
- Higher resource usage.

**Example**

```javascript
const ws = new WebSocket('ws://localhost:8000/gateway');

ws.onopen = () => {
  ws.send(JSON.stringify({
    op: 2,
    d: {
      token: 'Bot bot_token_here',
      properties: {
        os: 'linux',
        browser: 'my-bot',
        device: 'server'
      },
      intents: 3178237 // See Intents section; includes GUILDS | GUILD_MESSAGES | ARTIFACTS | SUMS
    }
  }));
};
```

The gateway path is `/gateway`, not `/ws` (`src/api/websocket/gateway.py:261`). The dev and preview proxies forward `/api` and `/gateway` to the API server.

### Use webhooks when you want serverless delivery

Choose webhooks when you need specific events without a persistent connection.

Inbound channel and server webhooks (posting messages *into* Plexichat) are implemented. See [Webhooks API](../api/webhooks.md).

Outbound application webhook delivery (Plexichat → your endpoint) is **not implemented**. There is no `POST /api/v1/applications/{application_id}/webhooks` creation route. Database tables for deliveries exist, but no HTTP API creates or manages them. Treat this as roadmap.

```http
POST /api/v1/applications/{application_id}/webhooks   # NOT IMPLEMENTED
Content-Type: application/json

{
  "url": "https://your-server.com/webhook",
  "events": ["MESSAGE_CREATE", "MESSAGE_UPDATE"]
}
```

## Configure intents

Intents declare which events your bot receives. Calculate the bitfield as a bitwise OR of the values you need.

| Bit | Value | Name | Description |
|---|---|---|---|
| 0 | `1 << 0` | `GUILDS` | Server create, update, delete |
| 1 | `1 << 1` | `GUILD_MEMBERS` | Member add, update, remove (privileged) |
| 2 | `1 << 2` | `GUILD_BANS` | Bans |
| 3 | `1 << 3` | `GUILD_EMOJIS` | Emoji updates |
| 4 | `1 << 4` | `GUILD_INTEGRATIONS` | Integrations |
| 5 | `1 << 5` | `GUILD_WEBHOOKS` | Webhook updates |
| 6 | `1 << 6` | `GUILD_INVITES` | Invites |
| 7 | `1 << 7` | `GUILD_VOICE_STATES` | Voice state updates |
| 8 | `1 << 8` | `GUILD_PRESENCES` | Presence updates (privileged) |
| 9 | `1 << 9` | `GUILD_MESSAGES` | Messages in servers |
| 10 | `1 << 10` | `GUILD_MESSAGE_REACTIONS` | Reactions |
| 11 | `1 << 11` | `GUILD_MESSAGE_TYPING` | Typing indicators |
| 12 | `1 << 12` | `DIRECT_MESSAGES` | Direct messages |
| 13 | `1 << 13` | `DIRECT_MESSAGE_REACTIONS` | DM reactions |
| 14 | `1 << 14` | `DIRECT_MESSAGE_TYPING` | DM typing |
| 15 | `1 << 15` | `MESSAGE_CONTENT` | Message content (privileged) |
| 16 | `1 << 16` | `SCHEDULED_EVENTS` | Scheduled events |
| 17 | `1 << 17` | `THREADS` | Threads |
| 18 | `1 << 18` | `AUTOMOD` | Auto-moderation events |
| 19 | `1 << 19` | `AUDIT_LOG` | Audit log entries |
| 20 | `1 << 20` | `ARTIFACTS` | Artifact fabric events (`src/core/events/types.py:120`) |
| 21 | `1 << 21` | `SUMS` | PlexiSum workbook events (`src/core/events/types.py:120`) |

Privileged intents require approval: `GUILD_MEMBERS` (`2`), `GUILD_PRESENCES` (`256`), and `MESSAGE_CONTENT` (`32768`). Request them from the server administrator.

Default intents include `ARTIFACTS` and `SUMS` in addition to the core guild and message intents. Example default bitmask `3178237` covers `GUILDS | GUILD_MESSAGES | ARTIFACTS | SUMS` and related baseline events.

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
  AUDIT_LOG: 1 << 19,
  ARTIFACTS: 1 << 20,
  SUMS: 1 << 21
};

const intents = Intents.GUILDS | Intents.GUILD_MESSAGES | Intents.ARTIFACTS | Intents.SUMS;
```

## Handle gateway opcodes

The gateway uses the opcodes in `src/api/websocket/opcodes.py:40`.

| Opcode | Name | Direction |
|---|---|---|
| `0` | `DISPATCH` | Server → client |
| `1` | `HEARTBEAT` | Client → server |
| `2` | `IDENTIFY` | Client → server |
| `3` | `PRESENCE_UPDATE` | Client → server |
| `4` | `VOICE_STATE_UPDATE` | Client → server |
| `6` | `RESUME` | Client → server |
| `7` | `RECONNECT` | Server → client |
| `8` | `REQUEST_GUILD_MEMBERS` | Client → server |
| `9` | `INVALID_SESSION` | Server → client |
| `10` | `HELLO` | Server → client |
| `11` | `HEARTBEAT_ACK` | Server → client |
| `20` | `VOICE_CONNECT` | Voice signaling |
| `21` | `VOICE_DISCONNECT` | Voice signaling |
| `22` | `VOICE_SDP_OFFER` | Voice signaling |
| `23` | `VOICE_SDP_ANSWER` | Voice signaling |
| `24` | `VOICE_ICE_CANDIDATE` | Voice signaling |
| `25` | `VOICE_SPEAKING` | Voice signaling |
| `26` | `VOICE_QUALITY` | Voice signaling |
| `30` | `INTERACTION_CREATE` | Dispatch for interactions |
| `40` | `TYPING_START` | Typing |
| `41` | `TYPING_STOP` | Typing |
| `50` | `RATCHET_UPDATE` | Channel ratchet |
| `60` | `ARTIFACT_SUBSCRIBE` | Artifact fabric |
| `61` | `ARTIFACT_UNSUBSCRIBE` | Artifact fabric |
| `62` | `ARTIFACT_OP` | Artifact fabric |
| `63` | `ARTIFACT_SYNC` | Artifact fabric |
| `64` | `SUM_SUBSCRIBE` | PlexiSum fabric |
| `65` | `SUM_UNSUBSCRIBE` | PlexiSum fabric |
| `66` | `SUM_OP` | PlexiSum fabric |
| `67` | `SUM_SYNC` | PlexiSum fabric |
| `68` | `ARTIFACT_OP_REJECTED` | Artifact fabric rejection |

## Work with slash commands

> **Not implemented (roadmap):** Command management does not exist yet. There are no `POST /api/v1/applications/{application_id}/commands` routes for registering, listing, or updating commands. The interaction-handling path below is implemented.

### Create a command (roadmap)

```http
POST /api/v1/applications/{application_id}/commands   # NOT IMPLEMENTED
Content-Type: application/json

{
  "name": "ping",
  "description": "Check bot latency",
  "type": 1
}
```

Command types: `1` chat input (slash command), `2` user context menu, `3` message context menu.

Option types: `3` string, `4` integer, `5` boolean, `6` user, `7` channel, `8` role, `9` mentionable.

### Handle interactions (implemented)

Interactions arrive as gateway dispatches with `op: 30` and `t: "INTERACTION_CREATE"`. Respond through the callback route:

```javascript
ws.onmessage = (event) => {
  const payload = JSON.parse(event.data);
  if (payload.op === 30 && payload.t === 'INTERACTION_CREATE') {
    const interaction = payload.d;
    if (interaction.type === 1) {
      handleCommand(interaction);
    }
  }
};

async function handleCommand(interaction) {
  const { name } = interaction.data;
  if (name === 'ping') {
    await respondToInteraction(interaction.token, {
      application_id: interaction.application_id,
      type: 4, // Channel message with source
      data: { content: 'Pong!' }
    });
  }
}

async function respondToInteraction(interactionToken, response) {
  await fetch(`/api/v1/applications/interactions/${interactionToken}/callback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(response)
  });
}
```

- Endpoint: `POST /api/v1/applications/interactions/{interaction_token}/callback`
- Success: `204 No Content`. The body must include `application_id`; the server verifies it matches the token and rate-limits per interaction token.
- Error responses: `400` invalid response, `404` unknown token or application binding, `409` already responded, `410` expired, `429` rate limited. All use `{"error":{"code":...,"message":"..."}}`.

Interaction response types: `1` pong, `4` channel message with source, `5` deferred channel message, `6` deferred update message, `7` update message.

## Send message components

Components are interactive UI elements in messages.

### Buttons

```javascript
await respondToInteraction(interaction.token, {
  type: 4,
  data: {
    content: 'Choose an option:',
    components: [
      {
        type: 1,
        components: [
          { type: 2, label: 'Option A', style: 1, custom_id: 'option_a' },
          { type: 2, label: 'Option B', style: 2, custom_id: 'option_b' }
        ]
      }
    ]
  }
});
```

Button styles: `1` primary, `2` secondary, `3` success, `4` danger, `5` link.

### Select menus

```javascript
await respondToInteraction(interaction.token, {
  type: 4,
  data: {
    content: 'Select a role:',
    components: [
      {
        type: 1,
        components: [
          {
            type: 3,
            custom_id: 'role_select',
            options: [
              { label: 'Moderator', value: 'mod_role_id', description: 'Can moderate messages' },
              { label: 'Member', value: 'member_role_id', description: 'Regular member' }
            ]
          }
        ]
      }
    ]
  }
});
```

## Handle rate limits

The API and gateway enforce rate limits to prevent abuse.

| Scope | Limit | Window | Burst |
|---|---|---|---|
| Global | 25,000 | 60 seconds | 5,000 |
| User | 10,000 | 60 seconds | 2,500 |
| IP | 5,000 | 60 seconds | 1,500 |
| Hourly (user) | 300,000 | 3,600 seconds | — |
| Daily (user) | 5,000,000 | 86,400 seconds | — |

Defaults are from `src/config_defaults.py:273`. Do not rely on hard-coded values in older docs. Override them in `config.yaml` and in Nginx if you change them.

Gateway-specific limits:

- Identify: 1 per 5 seconds per token.
- Resume: no limit.
- Other gateway payloads: 120 per 60 seconds (`src/config_defaults.py:168`).
- Bot requests: multiplied by `1.5` (`src/config_defaults.py:304`).

Check `X-RateLimit-Remaining` and `X-RateLimit-Reset`. When you receive `429`, read `Retry-After` and retry after that delay. Error bodies use `{"error":{"code":429,"message":"Too many requests"}}`.

```javascript
async function requestWithRetry(url, options) {
  while (true) {
    const response = await fetch(url, options);
    if (response.status === 429) {
      const retryAfter = response.headers.get('Retry-After');
      const delay = (parseInt(retryAfter, 10) || 5) * 1000;
      await new Promise((resolve) => setTimeout(resolve, delay));
      continue;
    }
    return response;
  }
}
```

## Install and manage bots on servers

Bots must be approved by a server administrator before they can join a server. The flow is request → review → approved installation.

### Approval workflow

1. A user discovers a bot in the directory.
2. The user requests the bot for a server.
3. An admin reviews and approves or denies the request.
4. The server installs the bot and shows it in the approved list with a BOT tag.

### Manage bots as an admin

**Approve a bot**

```http
POST /api/v1/bots/servers/{server_id}/approve
Authorization: Bearer YOUR_TOKEN
Content-Type: application/json

{
  "application_id": "123456789012345678",
  "permissions": "0",
  "bot_name": "My Utility Bot"
}
```

**List approved bots**

```http
GET /api/v1/bots/servers/{server_id}/approved
Authorization: Bearer YOUR_TOKEN
```

**Remove an approved bot**

```http
DELETE /api/v1/bots/servers/{server_id}/approved/{application_id}
Authorization: Bearer YOUR_TOKEN
```

### Request a bot as a user

**Request a bot**

```http
POST /api/v1/bots/servers/{server_id}/request
Authorization: Bearer YOUR_TOKEN
Content-Type: application/json

{
  "application_id": "123456789012345678",
  "reason": "This bot would help with moderation"
}
```

**Review a request (admin)**

```http
PUT /api/v1/bots/servers/{server_id}/requests/{request_id}
Authorization: Bearer YOUR_TOKEN
Content-Type: application/json

{
  "approve": true,
  "review_reason": "Approved, looks useful"
}
```

**View pending requests**

```http
GET /api/v1/bots/servers/{server_id}/requests?status_filter=pending
Authorization: Bearer YOUR_TOKEN
```

### Browse the bot directory

```http
GET /api/v1/bots/directory?limit=50&offset=0
GET /api/v1/bots/directory?server_id={server_id}&limit=50&offset=0
```

Use `server_id` to filter out bots already installed on that server.

### Manage bot profiles

**Get a profile**

```http
GET /api/v1/bots/profiles/{application_id}
```

**Update a profile**

```http
PUT /api/v1/bots/profiles/{application_id}
Authorization: Bearer YOUR_TOKEN
Content-Type: application/json

{
  "short_description": "A moderation bot with auto-flagging",
  "website_url": "https://mybot.example.com",
  "support_url": "https://discord.gg/mybotsupport",
  "tags": ["moderation", "automod", "security"],
  "nsfw": false,
  "private": false
}
```

### Manage authorized OAuth applications

**List authorized apps**

```http
GET /api/v1/bots/authorized-apps
Authorization: Bearer YOUR_TOKEN
```

**Revoke an app**

```http
DELETE /api/v1/bots/authorized-apps/{token_id}
Authorization: Bearer YOUR_TOKEN
```

### Admin panel

Server admins can manage bots at `/admin`:

1. Open `/admin` and go to the **Bots** tab.
2. Check totals for bots, approved installations, and pending requests.
3. Browse applications with approval counts.
4. Review pending requests.

### Licensing and limits

| Feature | Free tier | Premium tier |
|---|---|---|
| Max approved bots per server | 10 | 50 |
| Custom (non-curated) bots | Disabled | Enabled |
| Bot directory access | Read-only | Full access |
| Bot profile management | Limited | Full |

Limits are from `src/config_defaults.py:724` (`bots.max_per_server`, `bots.max_per_server_premium`).

## Use OAuth 2.0 (roadmap)

> **Not implemented:** Plexichat does not act as an OAuth 2.0 authorization server today. There is no `/oauth2/authorize` endpoint, so the classic `scope=bot&permissions=...` URL cannot be used. See [OAuth integration](../client-development/oauth-integration.md).

For reference, the planned (unimplemented) URL shape is:

```
https://your-server.com/oauth2/authorize?   # NOT IMPLEMENTED
  client_id=YOUR_APPLICATION_ID&
  redirect_uri=https://your-bot.com/callback&
  response_type=code&
  scope=bot&
  permissions=8&
  state=RANDOM_STATE
```

The only live OAuth routes are inbound provider sign-in flows for user login. They use PKCE `S256` and an exact `redirect_uri` allow-list:

```http
GET /api/v1/auth/oauth/{provider}/login?redirect_uri=...&code_challenge=...&code_challenge_method=S256
POST /api/v1/auth/oauth/{provider}/callback
```

Provide `code_challenge` (43–128 `A-Z a-z 0-9 - . _ ~` characters) with `code_challenge_method=S256` at initiation, and `code_verifier` at callback. The server sets an `HttpOnly` cookie `plexichat_oauth_state` and rejects mismatched `state` (`src/api/routes/auth/oauth.py:91`). See [Authentication API](../api/authentication.md#sign-in-with-oauth).

## Host a bot

### Run on your own server

```javascript
const { PlexiBot } = require('plexichat-bot');

const bot = new PlexiBot({
  token: 'Bot bot_token_here',
  intents: 3178237
});

bot.on('MESSAGE_CREATE', async (message) => {
  if (message.content === '!ping') {
    await bot.sendMessage(message.channel_id, 'Pong!');
  }
});

bot.start();
```

Deployment options: VPS (DigitalOcean, Linode, Vultr), dedicated server, or a home server with port forwarding.

### Deploy to a cloud platform

**AWS Lambda**

```javascript
exports.handler = async (event) => {
  const interaction = JSON.parse(event.body);
  // Handle interaction
  return { statusCode: 200, body: JSON.stringify(response) };
};
```

**Google Cloud Functions**

```javascript
exports.webhook = (req, res) => {
  const interaction = req.body;
  // Handle interaction
  res.json(response);
};
```

**Vercel**

```javascript
export default async function handler(req, res) {
  const interaction = req.body;
  // Handle interaction
  res.json(response);
}
```

### Use Docker

```dockerfile
FROM node:18-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install --production
COPY . .
CMD ["node", "index.js"]
```

```yaml
# docker-compose.yml
version: '3'
services:
  bot:
    build: .
    environment:
      - BOT_TOKEN=bot_token_here
    restart: unless-stopped
```

## Test a bot

### Test locally

```bash
# Start a local server
cd plexichat
python main.py

# In another terminal, run your bot
node bot.js
```

Your bot connects to `ws://localhost:8000/gateway` with `Authorization: Bot <token>` in the IDENTIFY payload.

### Write unit tests

```javascript
const { expect } = require('chai');

describe('Bot commands', () => {
  it('should respond to ping', async () => {
    const response = await handlePingCommand();
    expect(response.content).to.equal('Pong!');
  });
});
```

### Write integration tests

```javascript
describe('Bot integration', () => {
  it('should send a message', async () => {
    const message = await bot.sendMessage(testChannelId, 'Test');
    expect(message.content).to.equal('Test');
  });
});
```

## Deploy to production

### Configure environment variables

```bash
BOT_TOKEN=bot_token_here
CLIENT_ID=application_id
CLIENT_SECRET=client_secret
REDIRECT_URI=https://your-bot.com/callback
```

Never commit secrets to version control.

### Manage the process

**PM2**

```bash
npm install -g pm2
pm2 start bot.js --name my-bot
pm2 startup
pm2 save
```

**systemd**

```ini
[Unit]
Description=Plexichat Bot
After=network.target

[Service]
Type=simple
User=bot
WorkingDirectory=/opt/my-bot
ExecStart=/usr/bin/node /opt/my-bot/index.js
Restart=always
Environment=BOT_TOKEN=bot_token_here

[Install]
WantedBy=multi-user.target
```

### Monitor health

```javascript
app.get('/health', (req, res) => {
  res.json({
    status: 'healthy',
    uptime: process.uptime(),
    memory: process.memoryUsage()
  });
});
```

Use structured logging and expose metrics for uptime, gateway disconnects, and `429` counts.

## Follow best practices

**Security**

- Store tokens in environment variables.
- Validate all user input.
- Enforce rate limits.
- Use HTTPS for any webhook endpoint.
- Rotate tokens periodically.

**Performance**

- Cache frequently accessed data.
- Paginate large datasets.
- Use connection pooling.
- Use `async`/`await` correctly.

**Reliability**

- Implement reconnection logic for the gateway.
- Handle errors with the `{"error":{"code":...,"message":"..."}}` envelope.
- Use exponential backoff for retries.
- Monitor uptime and alert on failures.

**User experience**

- Return helpful error messages.
- Use slash commands for structured input.
- Implement autocomplete where available.
- Respond to interactions within three seconds or defer with type `5`.

## Example bot

```javascript
const { PlexiBot } = require('plexichat-bot');

const bot = new PlexiBot({
  token: process.env.BOT_TOKEN,
  intents: 3178237
});

bot.registerCommand({
  name: 'hello',
  description: 'Say hello',
  handler: async (interaction) => {
    await interaction.reply(`Hello, ${interaction.member.user.username}!`);
  }
});

bot.on('MESSAGE_CREATE', async (message) => {
  if (message.content === '!ping') {
    await bot.sendMessage(message.channel_id, 'Pong!');
  }
});

bot.start();
```

## Related documentation

- [API reference](../api/index.md)
- [WebSocket guide](../client-development/websocket.md)
- [OAuth scopes](../oauth-scopes.md) — regrouped by `PERMISSION_SCOPES` (`src/core/admin/permissions.py:42`)
- [Rate limits](../rate-limits.md) — defaults from `src/config_defaults.py:273`
- [Error codes](../errors.md) — envelope is `{"error":{"code":...,"message":"..."}}`
