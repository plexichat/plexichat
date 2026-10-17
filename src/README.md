# Plexichat source code

This directory contains the server implementation for Plexichat. The server
uses FastAPI and an asynchronous architecture. It supports SQLite for simple
deployments and PostgreSQL for production.

## Directory structure

| Directory | Description |
|-----------|-------------|
| `api/` | FastAPI application, route handlers, request and response schemas, and WebSocket gateway code in `api/websocket/`. Gateway opcodes live in `api/websocket/opcodes.py:8`. |
| `core/` | Business logic and data management for authentication, messaging, servers (guilds), artifacts, PlexiSum workbooks, and gateway events. Event types and intents live in `core/events/types.py:8`. |
| `utils/` | Shared utilities, such as logging and helpers. |
| `server/` | Server lifecycle, configuration loading, module initialization, and security checks. |
| `bootstrap/` | One-shot startup helpers for logging, utilities, and application creation. |
| `cli/` | Command-line argument parsing and subcommands such as `migrate-db`, `migrate-kek`, and `self-test`. |
| `static_client/` | Bundled static client assets for local development. |
| `tests/` | Automated tests. |

For the WebSocket contract that this code implements, read
`../docs/websocket/index.md`, `../docs/websocket/connection.md`,
`../docs/websocket/events.md`, `../docs/websocket/opcodes.md`, and
`../docs/websocket/intents.md`.

## Technical overview

*   **Framework** — FastAPI with async handlers.
*   **Databases** — SQLite or PostgreSQL. You select the database in the server
    configuration.
*   **Real-time layer** — A WebSocket gateway that uses dispatch events
    (`op: 0`) and signaling opcodes. The gateway supports heartbeats,
    resume, presence, voice signaling (`20`–`26`), typing (`40`–`41`),
    encryption ratchet (`50`), and collaborative fabric opcodes for artifacts
    (`60`–`63`) and PlexiSum workbooks (`64`–`67`).
*   **Event system** — Typed dispatch events defined in `core/events/types.py:8`
    and routed by intent in `core/events/router.py:13`. Clients declare which
    teams they want with intents in `IDENTIFY`.

## Where to start

1.  Read `api/websocket/opcodes.py:8` for the full opcode list and
    `core/events/types.py:8` for the full event and intent list.
2.  Inspect `core/events/gateway_emit.py:1` for how the server emits each
    event.
3.  Follow `api/websocket/connection.py` and `api/websocket/session.py` for
    the connect, hello, identify, heartbeat, and resume flow.
4.  Check `../docs/websocket/` for the narrative gateway documentation that
    stays in sync with the code.
