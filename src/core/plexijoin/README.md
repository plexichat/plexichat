# PlexiJoin federation

PlexiJoin provides server-to-server federation for Plexichat. Use it to create inter-instance links, approve join requests, and exchange HMAC-signed messages between independent instances.

## Overview

PlexiJoin manages outbound connections, inbound join requests, traffic accounting, and health checks. All cross-instance traffic is authenticated with HMAC-SHA256 signatures over a shared key that you exchange out-of-band.

Key concepts:

- **Connection** — An outbound link to a remote instance with a symmetric shared key. Lifecycle: `pending` → `active` → `broken`.
- **Join request** — An HMAC-signed request from a remote instance that requires admin approval.
- **Shared key** — A symmetric secret you generate and share out-of-band before federation traffic can flow.

## Architecture

| Component | File | Description |
|-----------|------|-------------|
| `PlexiJoinManager` | `manager.py` | Orchestrates connections, requests, transport, and analytics. |
| Schema | `schema.py` | Defines `plexijoin_connections`, `plexijoin_inbound_requests`, `plexijoin_traffic_log`, and `plexijoin_replay_nonces`. |

## Before you begin

- Ensure the encryption service is available. The manager encrypts shared keys at rest.
- Ensure admin logging is configured if you need audit trails for connection changes.
- Remote URLs must use `https` and resolve to a public IP. Private or reserved IPs are rejected.

## Manage outbound connections

### Create a connection

```python
from src.core.plexijoin import PlexiJoinManager

manager = PlexiJoinManager(db, admin_logger=logger, encryption_service=enc)

key = manager.generate_shared_key()
conn = manager.create_connection(
    remote_instance_id="remote-01",
    remote_url="https://remote.example.com",
    shared_key=key,
    note="Primary peer",
    admin_id=1,
)
# conn["status"] == "pending"
```

Share `key` with the remote instance out-of-band. Both sides must store the same key.

### List and get connections

```python
# Paginated list; each row includes a decrypted shared_key
page = manager.list_connections(status="active", page=1, per_page=50)

conn = manager.get_connection(connection_id=1)
if conn is None:
    print("Not found")
```

### Test and activate a connection

```python
# Health-probes remote_url; validates the URL for SSRF before the request.
# On success, transitions pending or broken connections to active.
# On failure, marks an active connection as broken.
result = await manager.test_connection(1)

# Idempotent activation
manager.activate_connection(1)
```

### Delete a connection

```python
manager.delete_connection(connection_id=1, admin_id=1)
```

## Send federation traffic

```python
# Send a signed join request to the remote /api/v1/federation/join-request
await manager.send_join_request(connection_id=1)

# Send a signed message envelope to /api/v1/federation/message
# Records outbound traffic on success; requires status == "active".
await manager.send_message(
    connection_id=1,
    message_type="chat.message",
    payload={"text": "Hello from instance A"},
)
```

All outbound POSTs include `x-plexijoin-signature`, `x-plexijoin-timestamp`, and `x-plexijoin-nonce` headers. The signature is `HMAC-SHA256(shared_key, body + timestamp + nonce)`.

## Handle inbound requests

`submit_inbound_request` is called only by the HMAC-authenticated inbound endpoint after signature verification.

```python
# Record a verified inbound request
req = manager.submit_inbound_request(
    remote_instance_id="remote-01",
    remote_url="https://remote.example.com",
    requested_by="admin@remote",
)

# Atomic approve or deny (WHERE status = 'pending' prevents concurrent double-approval)
manager.approve_request(request_id=1, admin_id=1)
manager.deny_request(request_id=1, admin_id=1)

# Approving also activates any matching pending outbound connection to the same remote.
```

List inbound requests with `list_requests(status=None, page=1, per_page=50)`.

## Monitor federation health

```python
summary = manager.get_status_summary()
# {"active_connections": 2, "broken_connections": 0, "pending_requests": 1, "messages_today": 42}

traffic = manager.get_traffic_data(hours=24)
manager.record_traffic(connection_id=1, direction="outbound", message_count=1)
```

## Validate remote URLs

```python
from src.core.plexijoin import validate_remote_url, RemoteURLBlockedError

try:
    validate_remote_url("https://remote.example.com")
except RemoteURLBlockedError as e:
    print(f"Blocked: {e}")
```

The validator requires `https` and uses `src.utils.security.URLValidator` to reject private or reserved IPs.

## HTTP endpoints

These routes are registered in the API layer. Admin routes require host restriction, an admin token, and the `admin.plexijoin.manage` permission. Federation routes use HMAC signatures, not JWT.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/admin/plexijoin/connections` | List connections |
| `GET` | `/api/v1/admin/plexijoin/connections/{id}` | Get one connection |
| `POST` | `/api/v1/admin/plexijoin/connections` | Create a connection |
| `DELETE` | `/api/v1/admin/plexijoin/connections/{id}` | Delete a connection |
| `POST` | `/api/v1/admin/plexijoin/connections/{id}/test` | Test connectivity |
| `GET` | `/api/v1/admin/plexijoin/requests` | List inbound requests |
| `POST` | `/api/v1/admin/plexijoin/requests/{id}/approve` | Approve a request |
| `POST` | `/api/v1/admin/plexijoin/requests/{id}/deny` | Deny a request |
| `GET` | `/api/v1/admin/plexijoin/status` | Health summary |
| `GET` | `/api/v1/admin/plexijoin/traffic` | Traffic data |
| `POST` | `/api/v1/federation/join-request` | Inbound join request (HMAC) |
| `POST` | `/api/v1/federation/message` | Inbound message (HMAC) |
| `POST` | `/api/v1/federation/artifact-op` | Artifact operation relay |
| `POST` | `/api/v1/federation/artifact-event` | Artifact event relay |

## Replay protection

The manager stores single-use nonces in `plexijoin_replay_nonces` and validates timestamps within a 300-second clock skew. Use `consume_replay_nonce(connection_id, nonce, timestamp)` to atomically claim a nonce for inbound verification.

Note: Keep `shared_key` values confidential. They are encrypted at rest but travel as the HMAC secret for every federation request.
