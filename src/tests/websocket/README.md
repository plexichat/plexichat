# WebSocket Tests

These tests verify the WebSocket gateway implementation, including connection
management, authentication, heartbeat handling, event dispatching, and session
resumption.

## Test files

- `test_connection.py` -- Manage connection state, lifecycle, rate limiting,
  JSON transport, latency, and keep-alive detection.
- `test_dispatch.py` -- Dispatch events to connections, filter by intents, and
  replay events after resume.
- `test_edge_cases.py` -- Handle unknown opcodes, close codes, compression,
  and handler edge cases.
- `test_heartbeat.py` -- Process heartbeat opcodes, track missed heartbeats,
  and measure latency.
- `test_identify.py` -- Authenticate clients with the IDENTIFY opcode,
  validate payloads, and enforce connection limits.
- `test_intents.py` -- Validate intent values, detect privileged intents, and
  filter events by intent flags.
- `test_rate_limit.py` -- Enforce per-connection event rate limits and verify
  dispatcher-level enforcement.
- `test_resume.py` -- Resume sessions, manage session state, replay events,
  and handle session timeouts.
