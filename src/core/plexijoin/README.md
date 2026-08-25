# PlexiJoin - Federation Module

Server-to-server federation for Plexichat. Enables inter-instance connections, join-request approval, and federated messaging between independent instances.

## Components

### PlexiJoinManager (`manager.py`)
Central orchestrator for federation operations:

Outbound connections:
- **`list_connections(status=None, page=1, per_page=50)`** - Paginated list (rows carry a decrypted `shared_key`)
- **`get_connection(connection_id)`** - One connection with decrypted `shared_key`, or `None`
- **`create_connection(remote_instance_id, remote_url, shared_key, note, admin_id, ...)`** - Create an outbound connection (`status="pending"`)
- **`generate_shared_key()`** - Cryptographically secure shared key for a new link
- **`delete_connection(connection_id, admin_id, ...)`** - Break and delete a connection
- **`test_connection(connection_id)`** *(async)* - Health-probe the remote; SSRF-validates `remote_url` first; success ACTIVATES a pending/broken connection, failure marks an active connection broken
- **`activate_connection(connection_id)`** - Idempotent transition to `active`

Federation transport:
- **`send_join_request(connection_id)`** *(async)* - POST this instance's HMAC-signed join request to the remote's `/api/v1/federation/join-request`
- **`send_message(connection_id, message_type, payload)`** *(async)* - POST an HMAC-signed message envelope to the remote's `/api/v1/federation/message`; records outbound traffic on success

Inbound requests:
- **`submit_inbound_request(remote_instance_id, remote_url, requested_by, note)`** - Record a verified inbound join request (`status="pending"`); called only by the HMAC-authenticated inbound endpoint
- **`approve_request(request_id, admin_id, ...)`** / **`deny_request(request_id, admin_id, ...)`** - ATOMIC conditional updates (`WHERE id = ? AND status = 'pending'`) so concurrent reviewers cannot both win; approving also activates any matching pending outbound connection

Status & analytics:
- **`get_status_summary()`**, **`get_traffic_data(hours=24)`**, **`record_traffic(connection_id, direction, message_count)`**

Validation:
- **`validate_remote_url(url)`** / **`RemoteURLBlockedError`** - SSRF hygiene: https scheme required, private/reserved IP resolution rejected (shared `URLValidator` from `src/utils/security.py`)

### Schema (`schema.py`)
SQL table definitions: `plexijoin_connections`, `plexijoin_inbound_requests`, `plexijoin_traffic_log`.

## HTTP Surface

Admin management (host-restricted, admin-token + `admin.plexijoin.manage` permission, license-gated):
- `GET  /api/v1/admin/plexijoin/connections[/{id}]`
- `POST /api/v1/admin/plexijoin/connections`
- `DELETE /api/v1/admin/plexijoin/connections/{id}`
- `POST /api/v1/admin/plexijoin/connections/{id}/test`
- `GET  /api/v1/admin/plexijoin/requests`
- `POST /api/v1/admin/plexijoin/requests/{id}/approve` | `/deny`
- `GET  /api/v1/admin/plexijoin/status` | `/traffic`

Client contract (`src/api/routes/join.py`, consumed by the web client's
`join.js`; same host restriction + admin permission gates):
- `GET  /api/v1/Join/list` - `{Joins: [...]}` keyed on `Join_id`
- `POST /api/v1/Join/request` - `{remote_url}` creates a pending connection and sends our signed join request
- `POST /api/v1/Join/{id}/message` - `{message_type, payload}` over an active Join
- `POST /api/v1/Join/{id}/break` - break a Join
- `POST /api/v1/Join/{id}/approve` - `{selected_servers}` approve an inbound request
- `POST /api/v1/Join/{id}/reject` - reject an inbound request

Cross-instance inbound transport (HMAC-SHA256 signed via the
`x-plexijoin-signature` header; no JWT — the signature is the credential):
- `POST /api/v1/federation/join-request` - verified requests become pending inbound rows
- `POST /api/v1/federation/message` - verified messages are acknowledged and accounted as inbound traffic
- `POST /api/v1/federation/artifact-op` | `/artifact-event` - artifact realtime fabric relay

## Key Concepts

- **Connection** - An instance-scoped outbound link with a symmetric shared key (`pending` -> `active` -> `broken`)
- **Join Request** - HMAC-signed request from a remote instance, recorded for admin approval
- **Trust bootstrap** - The shared key is exchanged out-of-band; both sides create connections with the same key before federation traffic flows
