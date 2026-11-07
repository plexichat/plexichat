# Load testing and capacity

This page documents the load-test scaffold and capacity figures for Plexichat. Use the scaffold to produce **measured, workload-specific** numbers for your deployment.

## Before you begin

- Run load tests against an **isolated, non-production** instance. The default stack exercises Argon2 password hashing, encryption, FTS indexing, and ClamAV — all CPU/RAM intensive.
- The “default recommended” Docker deployment is **1 backend container** (1 uvicorn asyncio process, no `workers`; `python main.py` / `src/server/lifecycle.py`), with **Postgres + Valkey + RustFS** and **ClamAV enabled** (`docker-compose.yml` defaults), **WebSocket compression on**, **`encrypt_messages` on**, and **write-time search indexing on**. Capacity is sensitive to these defaults.
- Capacity depends on workload shape: channel/member size (fanout), read/write ratio, message length/tokens, attachments, login burst rate, and compression/indexing settings.

## Scaffold

The scaffold lives at `plexichat/loadtest/` (run from the `plexichat/` directory with the project venv):

| File | Purpose |
|---|---|
| `config.py` | Shared env config (`PL_BASE_URL`, `PL_WS_URL`, user/channel, auth mode). |
| `provision.py` | One-time: register test users (409 treated as exists), create a server/channel, write `results/state.json`. Run **off** the measured path (registration is Argon2-heavy). |
| `bench_primitives.py` | Component micro-benchmarks (Argon2, AESGCM, BLAKE2b blind index/token indexing, JSON+zlib fanout encode, local SQLite FTS/token-row pattern). Writes `results/primitives.json`. **Not** end-to-end stack throughput. |
| `compute_envelope.py` | Derives a per-message CPU-primitive floor from `primitives.json` → `results/envelope.json`. **Not** end-to-end throughput. |
| `login_load.py` | Scenario 01: login/verify throughput & latency (Argon2-bound). |
| `ws_idle.py` | Scenario 02: connect `ws(s)://<host>/gateway` → HELLO → IDENTIFY (single-use `wst.*` ticket from `POST /api/v1/auth/ws-ticket`) → READY, heartbeat, hold idle connections. `PL_COMPRESS=1|0`. Max 5 connections/user. |
| `msg_send.py` | Scenario 03: receivers hold WS; sender posts `POST /api/v1/channels/{id}/messages` at fixed RPS. Measures send latency + WS `MESSAGE_CREATE` receipt (e2e) and fanout. Requires `PL_CHANNEL_ID`. Compare **DM/small channel vs large channel**. |
| `docker_stats_sample.sh` | Sample `docker stats` during a run. |

Dependencies used: `aiohttp`, `websockets`, `httpx`, `argon2-cffi`, `cryptography` (present in the `plexichat` venv).

### Provision

```bash
cd /root/code-plexichat/plexichat
PL_BASE_URL=http://localhost:8000/api/v1 \
PL_USER_COUNT=50 PL_USER_PREFIX=lt_ PL_USER_PASSWORD='LoadTest!Passw0rd#2026' \
  .venv/bin/python loadtest/provision.py
# writes loadtest/results/state.json (server_id, channel_id)
```

Notes: login establishes an HttpOnly session cookie; scripts send `X-Plexichat-Token-Mode: bearer` and fall back to the cookie jar as needed, then mint a single-use WS ticket. Ensure provisioned users are members of the target channel so server-channel fanout exercises the per-member `channels.view` permission filter (`messages_crud/broadcast._resolve_recipients`). Gateway is `ws(s)://<host>/gateway` (not under `/api/v1`).

### Run scenarios

```bash
# 01 login (Argon2) — 4 concurrent, 30s
PL_BASE_URL=http://localhost:8000/api/v1 PL_CONCURRENCY=4 PL_DURATION=30 PL_USER_COUNT=50 \
  .venv/bin/python loadtest/login_load.py

# 02 idle WS — hold 50 connections, 120s, compression on
PL_WS_URL=ws://localhost:8000/gateway PL_BASE_URL=http://localhost:8000/api/v1 \
  PL_USERS=50 PL_USER_COUNT=50 PL_HOLD=120 PL_COMPRESS=1 \
  .venv/bin/python loadtest/ws_idle.py

# 03 message send + fanout — 20 receivers, 5 msg/s, 60s
PL_WS_URL=ws://localhost:8000/gateway PL_BASE_URL=http://localhost:8000/api/v1 \
  PL_CHANNEL_ID=<from state.json> PL_RECEIVERS=20 PL_SEND_RPS=5 PL_DURATION=60 \
  .venv/bin/python loadtest/msg_send.py

# sample docker stats in parallel
./loadtest/docker_stats_sample.sh 120 loadtest/results/docker_stats.txt
```

### Reading results

- **Capacity point:** ramp `PL_USERS` / `PL_RECEIVERS` / `PL_SEND_RPS` / `PL_CONCURRENCY` until p95 latency grows sharply or errors/disconnects rise. Record that level with `docker stats` — that is the measured capacity for that mix.
- **Fanout sensitivity:** repeat `msg_send` for DM/small vs large channels and vary active recipients. Per-message cost scales with members checked × permission cost plus recipient connections × (JSON + zlib encode/send).
- **Interpretation:** treat REST accept latency and WS `MESSAGE_CREATE` end-to-end receipt separately; measure both (scenario 03 reports send and e2e).

## What drives cost (code paths)

- **Per message (send):** access/filter checks; **AES-256-GCM encrypt** (`ENC:v:...`, AAD = message id) when encryption enabled; **BLAKE2b blind index** (whole content + per-token search indexes); DB transaction (insert message, update conversation, delete/insert token rows, insert status, attachments); **write-time search indexing** (FTS delete+insert) when enabled; notification scheduling; cache/Valkey updates (`src/core/messaging/services/message/send.py`, `repositories/message.py`, `core/search/indexer/sqlite_fts.py`). Fanout: server channels filter recipients by per-member `channels.view` permission (`messages_crud/broadcast._resolve_recipients`); the dispatcher sends per target connection with JSON serialize and, when enabled, per-connection zlib (`api/websocket/dispatcher.py`, `connection.py`).
- **Per login:** Argon2id password verify (dominant CPU + 64 MiB transient per hash at defaults `t=3, m=65536 KiB, p=2`), plus blind-index lookups/throttle bookkeeping, session/token creation, device/IP tracking, audit (`src/core/auth/managers/session.py`, `utils/encryption`).
- **Per connected user:** 1–5 WS connections/user, heartbeat every 45 s, optional per-connection compression context; outbound budget 120 events/min/connection. Last disconnect persists offline and fans presence/typing updates to relevant users (`api/websocket/gateway.py`, `core/presence`).
- **Default stack RAM sharing:** backend + Postgres + Valkey + RustFS + **ClamAV** (+ nginx/backup) share host memory; ClamAV signature DB is a notable baseline on small hosts.

## Reference host: component measurements and estimated active users

**Measured (component primitives only, not a full-stack load test).** Host used for measurements (approximate): **~4 vCPUs, ~12 GB RAM allocated**, Linux x86_64. The Plexichat default Docker stack was **not** running end-to-end for these numbers; `bench_primitives.py` measured the actual crypto/codec primitives and a local SQLite FTS/token-row pattern with the project venv (`argon2-cffi`/`cryptography`), default Argon2 params, 240-char/40-token message sample, compression on. Raw output: `loadtest/results/primitives.json`, `loadtest/results/envelope.json`.

| Component (measured) | Result (this host) | Notes |
|---|---|---|
| Argon2id verify (`t=3,m=65536 KiB,p=2`) | **~555 ms mean / verify** (p95 ~551 ms), **~1.8 verifies/s** max per backend process | 64 MiB transient/hash; 1 asyncio backend process → effectively serialized. Dominates login. |
| Message AES-256-GCM encrypt (envelope) | **~12.0 µs** | AAD = message id. |
| BLAKE2b blind index (whole content) | **~2.5 µs** | Keyed index. |
| BLAKE2b token indexes (40 tokens) | **~64.4 µs total** (~1.6 µs/token) | Per searchable token. |
| Crypto subtotal (msg sample) | **~78.8 µs** | Encrypt + whole index + 40 token indexes. |
| Fanout encode per recipient connection | **~24.0 µs** (JSON + zlib, ~491 B payload) | Per-connection; excludes socket I/O, permission checks, DB. |
| Local SQLite FTS5 delete+insert (pattern) | **~259 µs** | **Local sqlite3 file, not Postgres/Valkey/txn/fsync/network** — relative only. |
| Local token-row delete+insert, 25 rows (pattern) | **~672 µs** (~1.33 ms scaled to 40 tokens) | **Relative only; production uses DB/transaction path.** |

Derived CPU-primitive floor per message (crypto + local DB pattern + encode only; **excludes** Postgres transaction/network, Valkey, FTS-via-Postgres, per-member `channels.view` permission checks, asyncio/uvicorn, fsync, socket I/O — **not** stack RPS): ~**1.44 ms (1 rcpt)**, ~**1.53 ms (5)**, ~**1.89 ms (20)**, ~**2.61 ms (50)**, ~**3.82 ms (100)**, ~**6.22 ms (200)** recipient connections.

**Estimated active/connected users on this reference host (default-like stack, 1 backend, ClamAV+compression+encrypt+write-time FTS on; not load-tested end-to-end):**

| Metric | Estimate (this host) | Primary limiter / assumptions |
|---|---|---|
| Sustained login verifies | **~1–2 / s** | Measured Argon2 on 1 process (64 MiB/hash). Bursts queue/latency-spike. |
| Concurrently connected (mostly idle, WS open) | **~200–600** | ~12 GB shared with Postgres/Valkey/RustFS/**ClamAV** (signature DB baseline is significant) + per-connection state/compression buffers/tasks. Measure with `ws_idle.py` + `docker stats`. |
| Concurrently active chat users (responsive, p95 acceptable) | **~30–100** | 1 asyncio backend process; DB txn + FTS/token-row writes + per-member permission checks + per-recipient JSON/zlib fanout. Assumes small-group/DM-heavy mix, ~1–3 messages/min/active user, typical message size. |
| Large-channel fanout | **materially lower** than the range above | Cost scales with members checked × permission cost + recipient connections × encode/send; test the real channel size. |

These are **estimates derived from measured primitives and code paths**, not a full-stack benchmark. Treat as order-of-magnitude guidance. For accurate numbers on a specific box (e.g. a 5600X/12 GB target) and workload, run `login_load.py`, `ws_idle.py`, and `msg_send.py` against that deployment at ramp, with `docker_stats_sample.sh`, and record the capacity knee. Re-run `bench_primitives.py` on the target host to normalize CPU differences.

## Related documentation

- [Performance tuning](../performance.md) — subsystem tuning (DB, Valkey, WebSocket, search, workers).
- [Database configuration](configuration/config-database.md)
- [Valkey configuration](configuration/config-valkey.md)
- [WebSocket configuration](configuration/config-websocket.md)
- [Search configuration](configuration/config-search.md)
- [Default configuration reference](../default-config.md)
