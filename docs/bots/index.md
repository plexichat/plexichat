# Bot Development Guide

This guide covers creating, hosting, and deploying Plexichat bots.

## Overview

A Plexichat bot is an automated user account that can interact with servers and channels via the API and WebSocket gateway. Bots can:
- Send and receive messages
- Manage servers and channels
- Respond to commands and interactions
- Automate moderation tasks
- Integrate with external services

## Creating a Bot Application

### Step 1: Create Application

Create an application to represent your bot:

```http
POST /api/v1/applications
Authorization: Bearer your_user_token
Content-Type: application/json

{
  "name": "My Awesome Bot",
  "description": "A bot that does awesome things",
  "bot_public": true,
  "bot_require_code_grant": false
}
```

Response (`ApplicationResponse`):
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
- **No `client_secret` is returned** by create. Secrets are managed separately via
  `POST /api/v1/applications/{application_id}/regenerate-secret` (returns `{"secret": ...}`).
- `redirect_uris` are request-only fields on create/update; they are **not** echoed back
  in the response.
- **No bot account is created automatically** — `bot_id` starts as `null` and `bot` is
  `false` until you explicitly create the bot in the next step.

### Step 2: Create Bot Account

A bot account must be created explicitly. On success the application's `bot_id`
(the bot's user ID) is set. Creating a second bot for the same application fails
with HTTP 400 ("A bot already exists for this application").

### Step 3: Generate Bot Token

Generate a token for the bot to authenticate:

```http
POST /api/v1/applications/{application_id}/bot
Authorization: Bearer your_user_token
```

Response:
```json
{
  "token": "bot_token_here",
  "bot_id": "987654321098765432"
}
```

**Important:** Store the bot token securely. It grants full access to the bot account.

## Bot Authentication

Bots authenticate using the bot token:

```http
Authorization: Bot bot_token_here
```

Note the "Bot " prefix before the token.

## WebSocket vs Webhooks

Bots can receive events via WebSocket gateway or webhooks.

### WebSocket Gateway

**Use WebSocket when:**
- You need real-time event delivery
- You need to send messages immediately
- You need low latency
- You have a persistent connection

**Pros:**
- Real-time events
- Low latency
- Full event access
- Can send messages anytime

**Cons:**
- Requires persistent connection
- More complex implementation
- Higher resource usage

**Example:**
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
      intents: 513  // GUILDS | GUILD_MESSAGES
    }
  }));
};
```

### Webhooks

**Use Webhooks when:**
- You only need specific events
- You want serverless deployment
- You have intermittent connectivity
- You prefer simpler implementation

**Pros:**
- No persistent connection needed
- Serverless-friendly
- Simpler implementation
- Lower resource usage

**Cons:**
- Higher latency
- Limited event types
- Cannot send messages via webhook
- Requires external endpoint

**Example:**
Application webhook delivery (server → your endpoint) is **NOT IMPLEMENTED**.
There is no `POST /api/v1/applications/{application_id}/webhooks` creation route
today. The database tables for application webhook deliveries already exist, but
no HTTP API currently creates or manages these webhooks — treat this as roadmap,
not an available feature.

Inbound channel/server webhooks (posting messages *into* Plexichat from external
services) are a separate, implemented feature — see [Webhooks API](../api/webhooks.md).

```http
POST /api/v1/applications/{application_id}/webhooks   # NOT IMPLEMENTED
Content-Type: application/json

{
  "url": "https://your-server.com/webhook",
  "events": ["MESSAGE_CREATE", "MESSAGE_UPDATE"]
}
```

## Bot Intents

Bots must specify which events they want to receive:

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

**Privileged Intents:**
- `GUILD_MEMBERS` (2) - Requires approval
- `GUILD_PRESENCES` (256) - Requires approval
- `MESSAGE_CONTENT` (32768) - Requires approval

Request privileged intents from your server administrator.

## Slash Commands

> **NOT IMPLEMENTED — ROADMAP:** Slash-command *management* does not exist yet.
> There are no `POST /api/v1/applications/{application_id}/commands` (or similar)
> routes for registering, listing, or updating commands. The interaction-handling
> half below (the callback route and response types) **is** implemented and
> documented for when command registration ships.

### Creating a Command (Roadmap)

```http
POST /api/v1/applications/{application_id}/commands   # NOT IMPLEMENTED
Content-Type: application/json

{
  "name": "ping",
  "description": "Check bot latency",
  "type": 1
}
```

Command types:
- `1` - Chat input (slash command)
- `2` - User context menu
- `3` - Message context menu

### Command Options (Roadmap)

```http
POST /api/v1/applications/{application_id}/commands   # NOT IMPLEMENTED
Content-Type: application/json

{
  "name": "ban",
  "description": "Ban a user",
  "type": 1,
  "options": [
    {
      "name": "user",
      "description": "User to ban",
      "type": 6,
      "required": true
    },
    {
      "name": "reason",
      "description": "Reason for ban",
      "type": 3,
      "required": false
    }
  ]
}
```

Option types:
- `3` - String
- `4` - Integer
- `5` - Boolean
- `6` - User
- `7` - Channel
- `8` - Role
- `9` - Mentionable

### Handling Interactions (Implemented)

When an interaction is dispatched to your bot (gateway event
`INTERACTION_CREATE`, op `30`), respond via the interactions callback route:

```javascript
ws.onmessage = (event) => {
  const payload = JSON.parse(event.data);

  if (payload.op === 30 && payload.t === 'INTERACTION_CREATE') {
    const interaction = payload.d;

    if (interaction.type === 1) { // Application command
      handleCommand(interaction);
    }
  }
};

async function handleCommand(interaction) {
  const { name, options } = interaction.data;

  if (name === 'ping') {
    await respondToInteraction(interaction.token, {
      application_id: interaction.application_id,
      type: 4,  // Channel message with source
      data: {
        content: 'Pong!'
      }
    });
  }
}

async function respondToInteraction(interactionToken, response) {
  await fetch(`/api/v1/applications/interactions/${interactionToken}/callback`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(response)
  });
}
```

The callback endpoint is
`POST /api/v1/applications/interactions/{interaction_token}/callback` and returns
HTTP 204 on success. The JSON body must include the interaction's
`application_id`; the server verifies that it matches the token before accepting
the response. Callback attempts are also rate limited per interaction token.
Error responses: `400` invalid response, `404` unknown token or application
binding, `409` interaction already responded to, `410` interaction expired, and
`429` callback rate limit exceeded.

### Interaction Response Types

- `1` - Pong (acknowledge without response)
- `4` - Channel message with source (reply in channel)
- `5` - Deferred channel message (show loading, edit later)
- `6` - Deferred update message (edit original message)
- `7` - Update message (edit original message)

## Message Components

Components allow interactive UI elements in messages.

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
          {
            type: 2,
            label: 'Option A',
            style: 1,
            custom_id: 'option_a'
          },
          {
            type: 2,
            label: 'Option B',
            style: 2,
            custom_id: 'option_b'
          }
        ]
      }
    ]
  }
});
```

Button styles:
- `1` - Primary (blue)
- `2` - Secondary (gray)
- `3` - Success (green)
- `4` - Danger (red)
- `5` - Link (gray, opens URL)

### Select Menus

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
              {
                label: 'Moderator',
                value: 'mod_role_id',
                description: 'Can moderate messages'
              },
              {
                label: 'Member',
                value: 'member_role_id',
                description: 'Regular member'
              }
            ]
          }
        ]
      }
    ]
  }
});
```

## Rate Limiting

Bots are subject to rate limits to prevent abuse.

### Global Rate Limits

- **Identify**: 1 per 5 seconds per token
- **Resume**: No limit
- **Other gateway payloads**: 120 per 60 seconds

### API Rate Limits

API endpoints have individual rate limits. Check the `X-RateLimit-Remaining` and `X-RateLimit-Reset` headers.

### Handling Rate Limits

```javascript
async function requestWithRetry(url, options) {
  while (true) {
    const response = await fetch(url, options);

    if (response.status === 429) {
      const retryAfter = response.headers.get('Retry-After');
      const delay = (parseInt(retryAfter) || 5) * 1000;
      await new Promise(resolve => setTimeout(resolve, delay));
      continue;
    }

    return response;
  }
}
```

## Bot Hosting

### Self-Hosted

Run your bot on your own server:

```javascript
const { PlexiBot } = require('plexichat-bot');

const bot = new PlexiBot({
  token: 'Bot bot_token_here',
  intents: 513
});

bot.on('MESSAGE_CREATE', async (message) => {
  if (message.content === '!ping') {
    await bot.sendMessage(message.channel_id, 'Pong!');
  }
});

bot.start();
```

**Deployment Options:**
- VPS (DigitalOcean, Linode, Vultr)
- Dedicated server
- Home server (with port forwarding)

### Cloud Platforms

Deploy to serverless platforms:

**AWS Lambda:**
```javascript
exports.handler = async (event) => {
  const interaction = JSON.parse(event.body);
  // Handle interaction
  return {
    statusCode: 200,
    body: JSON.stringify(response)
  };
};
```

**Google Cloud Functions:**
```javascript
exports.webhook = (req, res) => {
  const interaction = req.body;
  // Handle interaction
  res.json(response);
};
```

**Vercel:**
```javascript
export default async function handler(req, res) {
  const interaction = req.body;
  // Handle interaction
  res.json(response);
}
```

### Docker

Containerize your bot:

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

## Bot Installation & Approval Workflow

Bots must be approved by a server administrator before they can interact with a server. This two-step process ensures bot security and prevents unauthorized installations.

### Approval Workflow

1. **User discovers a bot** via the bot directory or external link
2. **User requests the bot** on a server (or an admin directly approves it)
3. **Admin reviews the request** and approves or denies it
4. **Once approved**, the bot is automatically installed on the server
5. **Bot appears** in the server's approved bots list with a BOT tag

### Server Bot Management (Admin)

Navigate to **Server Settings > Bots** to manage bots:

**Approve a Bot:**
```http
POST /api/v1/bots/servers/{server_id}/approve
Authorization: Bearer your_token
Content-Type: application/json

{
  "application_id": 123456789,
  "permissions": "0",
  "bot_name": "My Utility Bot"
}
```

**List Approved Bots:**
```http
GET /api/v1/bots/servers/{server_id}/approved
Authorization: Bearer your_token
```

**Remove an Approved Bot:**
```http
DELETE /api/v1/bots/servers/{server_id}/approved/{application_id}
Authorization: Bearer your_token
```

### Bot Request Flow (Users)

Users can request that a bot be added to a server:

**Request a Bot:**
```http
POST /api/v1/bots/servers/{server_id}/request
Authorization: Bearer your_token
Content-Type: application/json

{
  "application_id": 123456789,
  "reason": "This bot would help with moderation"
}
```

**Admin Reviews Request:**
```http
PUT /api/v1/bots/servers/{server_id}/requests/{request_id}
Authorization: Bearer your_token
Content-Type: application/json

{
  "approve": true,
  "review_reason": "Approved, looks useful"
}
```

**View Pending Requests:**
```http
GET /api/v1/bots/servers/{server_id}/requests?status_filter=pending
Authorization: Bearer your_token
```

### Bot Directory

Browse available bots:
```http
GET /api/v1/bots/directory?limit=50&offset=0
```

Optionally filter out already-installed bots for a server:
```http
GET /api/v1/bots/directory?server_id={server_id}&limit=50&offset=0
```

### Bot Profiles

Bot owners can customize their bot's public profile:

**Get Profile:**
```http
GET /api/v1/bots/profiles/{application_id}
```

**Update Profile:**
```http
PUT /api/v1/bots/profiles/{application_id}
Authorization: Bearer your_token
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

### Authorized Applications

Users can view and revoke OAuth2 applications they've authorized:

**List Authorized Apps:**
```http
GET /api/v1/bots/authorized-apps
Authorization: Bearer your_token
```

**Revoke an App:**
```http
DELETE /api/v1/bots/authorized-apps/{token_id}
Authorization: Bearer your_token
```

### Admin Panel

Server administrators can manage bots across all servers via the admin dashboard:

1. Log into the admin panel at `/admin`
2. Navigate to the **Bots** tab
3. View statistics: total bots, approved installations, pending requests
4. Browse all applications with their approval counts
5. Review and manage pending bot requests

### Licensing & Limits

Bot features are gated by the instance license:

| Feature | Free Tier | Premium Tier |
|---|---|---|
| Max approved bots per server | 10 | 50 |
| Custom (non-curated) bots | Disabled | Enabled |
| Bot directory access | Read-only | Full access |
| Bot profile management | Limited | Full |

## Bot Authorization via OAuth2

> **NOT IMPLEMENTED — ROADMAP:** Plexichat does not currently act as an OAuth2
> authorization server. There is no `/oauth2/authorize` endpoint, so the classic
> "add bot to server" authorization URL (`scope=bot&permissions=...`) **cannot be
> used**. See [oauth-integration.md](../client-development/oauth-integration.md).
>
> The implemented way to install a bot on a server is the request/approval
> workflow described in
> [Bot Installation & Approval Workflow](#bot-installation--approval-workflow)
> above.

For reference, the planned (unimplemented) URL shape is:

```
https://your-server.com/oauth2/authorize?   # NOT IMPLEMENTED
  client_id=your_application_id&
  redirect_uri=https://your-bot.com/callback&
  response_type=code&
  scope=bot&
  permissions=8&
  state=random_state_string
```

### Handling OAuth Callback

The only live OAuth routes are *inbound* provider sign-in flows, which are for
user login, not bot installation:

```http
POST /api/v1/auth/oauth/{provider}/callback?code=authorization_code&state=state_token
```

Plexichat handles the code-to-token exchange with the provider internally and
creates a user session. No additional token exchange is required on your end.
See [Authentication API](../api/authentication.md#oauth-sign-in).

## Testing

### Local Testing

Test your bot against a local Plexichat server:

```bash
# Start local server
cd plexichat
python main.py

# Run your bot
node bot.js
```

### Unit Testing

Test bot logic in isolation:

```javascript
const { expect } = require('chai');

describe('Bot Commands', () => {
  it('should respond to ping', async () => {
    const response = await handlePingCommand();
    expect(response.content).to.equal('Pong!');
  });
});
```

### Integration Testing

Test bot against test server:

```javascript
describe('Bot Integration', () => {
  it('should send message', async () => {
    const message = await bot.sendMessage(testChannelId, 'Test');
    expect(message.content).to.equal('Test');
  });
});
```

## Deployment

### Environment Variables

Store sensitive configuration in environment variables:

```bash
BOT_TOKEN=bot_token_here
CLIENT_ID=application_id
CLIENT_SECRET=client_secret
REDIRECT_URI=https://your-bot.com/callback
```

### Process Management

Use process managers for production:

**PM2:**
```bash
npm install -g pm2
pm2 start bot.js --name my-bot
pm2 startup
pm2 save
```

**Systemd:**
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

### Monitoring

Monitor your bot health:

**Health Checks:**
```javascript
app.get('/health', (req, res) => {
  res.json({
    status: 'healthy',
    uptime: process.uptime(),
    memory: process.memoryUsage()
  });
});
```

**Logging:**
```javascript
const winston = require('winston');

const logger = winston.createLogger({
  level: 'info',
  format: winston.format.json(),
  transports: [
    new winston.transports.File({ filename: 'error.log', level: 'error' }),
    new winston.transports.File({ filename: 'combined.log' })
  ]
});
```

## Best Practices

### Security

- Never commit bot tokens to version control
- Use environment variables for secrets
- Validate all user input
- Implement rate limiting
- Use HTTPS for webhooks
- Rotate tokens periodically

### Performance

- Cache frequently accessed data
- Use pagination for large datasets
- Implement connection pooling
- Optimize database queries
- Use async/await properly

### Reliability

- Implement reconnection logic
- Handle errors gracefully
- Use exponential backoff for retries
- Monitor bot uptime
- Set up alerts for failures

### User Experience

- Provide helpful error messages
- Use slash commands for structured input
- Implement autocomplete for commands
- Provide usage instructions
- Respond quickly to interactions

## Example Bot

```javascript
const { PlexiBot } = require('plexichat-bot');

const bot = new PlexiBot({
  token: process.env.BOT_TOKEN,
  intents: 513  // GUILDS | GUILD_MESSAGES
});

// Register slash commands
bot.registerCommand({
  name: 'hello',
  description: 'Say hello',
  handler: async (interaction) => {
    await interaction.reply(`Hello, ${interaction.member.user.username}!`);
  }
});

// Message handler
bot.on('MESSAGE_CREATE', async (message) => {
  if (message.content === '!ping') {
    await bot.sendMessage(message.channel_id, 'Pong!');
  }
});

// Start bot
bot.start();
```

## Resources

- [API Reference](../api/index.md)
- [WebSocket Guide](../client-development/websocket.md)
- [OAuth Scopes](../oauth-scopes.md)
- [Rate Limits](../rate-limits.md)
- [Error Codes](../errors.md)
