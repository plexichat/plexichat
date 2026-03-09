# Getting Started

Use this guide when bringing up a client, bot, or integration against a Plexichat server.

## 1. Learn the Runtime Surface

- REST API base: `{{BASE_URL}}`
- WebSocket gateway: `{{WEBSOCKET_URL}}`
- generated API docs: `/docs`
- health endpoint: `/health`

## 2. Check Version Compatibility

Call the public version endpoints before relying on feature-specific behavior.

```bash
curl {{BASE_URL}}/version
curl -X POST {{BASE_URL}}/version/negotiate \
  -H "Content-Type: application/json" \
  -d '{"client_version":"{{VERSION}}"}'
```

## 3. Discover Server Capabilities

`GET /capabilities` exposes public constants such as avatar limits and whether the server requires an additional access-token gate.

```bash
curl {{BASE_URL}}/capabilities
```

## 4. Authenticate

Most REST endpoints require `Authorization: Bearer <token>` for user sessions or `Authorization: Bot <token>` for bot tokens.

Typical first steps:

```bash
curl -X POST {{BASE_URL}}/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"example","password":"example"}'

curl {{BASE_URL}}/users/@me \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

If the server enforces an additional API access token, include `X-API-Access-Token` on authenticated requests. See [Access Tokens](admin-access-tokens.md).

## 5. Make a First Authenticated Request

```bash
curl {{BASE_URL}}/users/@me \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

Good follow-up requests are:

- `GET /users/@me/settings`
- `GET /users/@me/features`
- `GET /servers`
- `GET /users/@me/notifications`

## 6. Connect to the Gateway

The gateway is used for real-time events, presence, typing, and voice signaling.

High-level flow:

1. connect to `{{WEBSOCKET_URL}}`
2. receive `HELLO`
3. send `IDENTIFY`
4. start heartbeating
5. consume `DISPATCH` events such as `READY` and `MESSAGE_CREATE`

See [Gateway Connection](websocket/connection.md) for payload examples.

## 7. Use the Right Documentation Surface

- Use this portal for narrative guidance and route-group overviews.
- Use `/docs` or `/openapi.json` when you need the generated request and response schemas for a specific endpoint.

## 8. Next Reading

- [API Reference](api/index.md)
- [WebSocket Events](websocket/events.md)
- [Rate Limits](rate-limits.md)
