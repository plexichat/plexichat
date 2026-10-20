# Client development guide

Build clients, bots, or integrations against the Plexichat API and gateway. This guide points you to the task-oriented docs you need for each integration step.

## Before you begin

Complete the [getting started guide](../getting-started.md) to create an account, obtain a token, make your first REST request, and open a gateway connection. You need a valid session token or bot token before you can use the gateway.

## What to read next

Use this table to find the right doc for your task.

| Task | Doc |
| --- | --- |
| Authenticate users, manage sessions, bots, or 2FA | [Authentication configuration](../deployment/configuration/config-authentication.md) |
| Call REST endpoints | [API reference](../api/index.md) |
| Stream real-time events | [WebSocket gateway](websocket.md) |
| Handle rate limits and `429` responses | [Rate limits](../rate-limits.md) |
| Work with IDs, timestamps, and common shapes | [Data types](../data-types.md) |
| Handle errors consistently | [Errors](../errors.md) |
| Choose OAuth scopes for your app | [OAuth scopes](../oauth-scopes.md) |
| Add inbound sign-in with Google, GitHub, Microsoft, or GitLab | [OAuth integration](oauth-integration.md) |

## Gateway quick link

If you are building a real-time client, start with [WebSocket Gateway](websocket.md) for the connection flow, heartbeat handling, and opcode and intent reference, then see [Data types](../data-types.md) for Snowflake IDs and timestamp formats used in gateway payloads.

## API quick link

If you are building a REST-only integration, start with the [API reference](../api/index.md). Every route group lists its path, method, auth requirements, and rate-limit tier. The [configuration overview](../configuration.md) describes how the server discovers and merges your `config.yaml` file.

## Related docs

- [Configuration overview](../configuration.md)
- [Default configuration reference](../default-config.md)
- [WebSocket opcodes](../websocket/opcodes.md)
- [WebSocket intents](../websocket/intents.md)
