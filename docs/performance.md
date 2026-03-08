# Performance Guidance

This page focuses on application-level performance characteristics that are visible from the codebase without prescribing infrastructure-specific tuning.

## Performance-Sensitive Areas

- message creation and event fanout
- WebSocket heartbeat stability and reconnect behavior
- search queries and pagination
- attachment uploads and chunked upload completion
- notification fanout and presence updates
- media hash checks and compression work

## Practical Client Guidance

- page and paginate instead of requesting large collections at once
- reuse authenticated sessions rather than repeatedly logging in
- respect rate limits and avoid aggressive polling when gateway events are available
- use the upload-session flow for larger media instead of assuming single-request uploads
- consume the status and version endpoints for lightweight health signals

## Practical Operator Guidance

- monitor latency on message, upload, and search-heavy routes
- validate WebSocket stability under reconnect scenarios
- review rate-limit settings when traffic patterns change
- verify that media and search subsystems remain responsive under load

## Helpful Endpoints

| Endpoint | Use |
|----------|-----|
| `/health` | quick readiness signal |
| `/api/v1/status` | availability and maintenance state |
| `/api/v1/version` | compatibility and version checks |
| `/api/v1/media/upload/sessions` | in-progress upload visibility |
| `/api/v1/media/compression/status` | media-processing status |

## Scope

Detailed infrastructure benchmarking and environment-specific tuning belong in internal operations documentation rather than this served docs surface.
