# Plexichat load-test scaffold

Measures capacity of a **default-like** Plexichat deployment (1 backend, uvicorn asyncio, no `--workers`; Postgres+Valkey+RustFS+ClamAV on) by isolating cost buckets. **Do not run against a shared/production instance.**

## What this gives you
- `bench_primitives.py` — **accurate component measurements on the current host** (Argon2, AESGCM encrypt, BLAKE2b blind index/token indexing, FTS5/token-row SQLite patterns, JSON+zlib fanout encoding). *Not* end-to-end stack throughput.
- `provision.py` — create test users + a server/channel (run once, **off** the measured path; registration/login are Argon2-heavy).
- `login_load.py` — scenario 01: login/verify throughput & latency (Argon2-bound).
- `ws_idle.py` — scenario 02: connect + HELLO/IDENTIFY + heartbeat, hold N idle connections (RAM/connection stability). `PL_COMPRESS=1|0`.
- `msg_send.py` — scenario 03: receivers hold WS; sender posts `POST /channels/{id}/messages` at fixed RPS (per-message + DB/FTS + permission-filtered fanout). Requires `PL_CHANNEL_ID`. Compare **DM/small channel vs large channel** (member count drives per-member `channels.view` checks + per-connection zlib/serialize in `broadcast/dispatcher`).
- `docker_stats_sample.sh` — sample `docker stats` during a run.

For **true end-to-end accurate figures** for the *5600X/12GB default recommended Docker* target: run these scenarios against that actual deployment (same compose/images/config), on that host, and record `docker stats` + scenario JSON. Component numbers here do **not** include Postgres network/txn, permission checks, Valkey, uvicorn/asyncio overhead, ClamAV contention, or socket I/O.

## Prereqs
- Plexichat venv deps present (`aiohttp`, `websockets`, `httpx`, `argon2-cffi`, `cryptography`). Run from repo:
  ```bash
  cd /root/code-plexichat/plexichat
  ```
- A running target stack (default compose). Note: 1 backend worker, `encrypt_messages` + write-time FTS + ClamAV on = the “default” you’re measuring.

## Provision (once)
```bash
PL_BASE_URL=http://localhost:8000/api/v1 \
PL_USER_COUNT=50 PL_USER_PREFIX=lt_ PL_USER_PASSWORD='LoadTest!Passw0rd#2026' \
  .venv/bin/python loadtest/provision.py
# writes loadtest/results/state.json (server_id, channel_id)
# If join attempts show 0, add provisioned users to the channel per your build's API
# so fanout/permission-filter is exercised.
```
Auth note: login sets an HttpOnly session cookie; scripts send `X-Plexichat-Token-Mode: bearer` (and fall back to cookie jar) to obtain tokens for `POST /auth/ws-ticket` → gateway IDENTIFY (`wst.*`, single-use). Gateway is `ws(s)://<host>/gateway` (not under `/api/v1`).

## Scenarios
```bash
# 01 login (Argon2) — 4 concurrent, 30s
PL_BASE_URL=http://localhost:8000/api/v1 PL_CONCURRENCY=4 PL_DURATION=30 PL_USER_COUNT=50 \
  .venv/bin/python loadtest/login_load.py

# 02 idle WS (hold 50 conns, 120s, compression on)
PL_WS_URL=ws://localhost:8000/gateway PL_BASE_URL=http://localhost:8000/api/v1 \
  PL_USERS=50 PL_USER_COUNT=50 PL_HOLD=120 PL_COMPRESS=1 \
  .venv/bin/python loadtest/ws_idle.py

# 03 message send+fanout (20 receivers, 5 msg/s, 60s)
PL_WS_URL=ws://localhost:8000/gateway PL_BASE_URL=http://localhost:8000/api/v1 \
  PL_CHANNEL_ID=<from state.json> PL_RECEIVERS=20 PL_SEND_RPS=5 PL_DURATION=60 \
  .venv/bin/python loadtest/msg_send.py

# sample docker stats in parallel
./loadtest/docker_stats_sample.sh 120 loadtest/results/docker_stats.txt
```

## Reading results
- **Capacity point:** ramp `PL_USERS`/`PL_RECEIVERS`/`PL_SEND_RPS`/`PL_CONCURRENCY` until p95 grows sharply or `err`/disconnects rise; capture that level + `docker stats` = measured capacity for that mix.
- **Fanout sensitivity:** repeat `msg_send` with small vs large `member` channels (and vary active recipients). Cost scales with members checked × permission cost + recipient connections × (JSON+zlib).
- **Defaults that matter:** 1 asyncio backend process; `websocket.compression_enabled`/per-connection zlib; `encrypt_messages` on; `search.write_time_indexing` on (FTS delete+insert per msg); ClamAV enabled (baseline RAM/CPU; mostly upload-path). 120 events/min/conn outbound budget, 5 WS conns/user.

## Component figures (this host, primitive-only)
Run ` .venv/bin/python loadtest/bench_primitives.py` → `loadtest/results/primitives.json`. These are the code-path primitives (Argon2 t=3/m=64MiB/p=2; AESGCM; BLAKE2b blind index; zlib; SQLite FTS/token-row pattern) measured on *this* host — use as per-op lower-bound ingredients, not stack RPS.
