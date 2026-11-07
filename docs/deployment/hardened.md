# Hardened deployment profile (FI-like)

This runbook describes a concrete, validated-as-possible hardened deployment profile for Plexichat in financial-institution-like (FI-like) environments. It builds on [admin hardening](../admin-hardening.md) and the measured figures in [load testing and capacity](load-testing.md).

> **Scope and honesty:** this profile hardens configuration and deployment topology (TLS termination, strict origins/hosts/CORS, admin restrictions, ClamAV, least-privilege credentials, audit/retention, secrets handling). It is not, by itself, a compliance certification. Validate controls against your regulator/framework (e.g., SOC 2, PCI DSS, GLBA), your threat model, and your change-management process before production use.

## Before you begin

- Deploy from the `plexichat/` directory with the project venv and Docker Compose v2.
- Use a **non-production** environment first. Capacity and scanning behavior are workload-sensitive (see [load-testing.md](load-testing.md)).
- The Docker supply-chain policy in `docker-compose.yml` applies: pin released tags, verify image digests/SHAs out of band, keep `.env` `chmod 600` (`umask 077`), and never commit secrets.
- `NO_STRICT_CONFIG` must remain `false` in production. The server enforces security checks in production (`src/server/security_checks.py`); placeholder/default security keys/passwords abort production boot. Set `application.environment: production` (hardened config does this).

## Profile artifacts (concrete)

All paths below are relative to `plexichat/`:

| Artifact | Purpose |
|---|---|
| [`config/hardened.yaml`](../../config/hardened.yaml) | Secure config overlay consistent with `src/config_defaults.py` / `src/config_defaults_authentication.py`. Sets production, strict admin, CORS/origin allowlist placeholders, proxy trust, TLS-at-edge posture, Postgres/Valkey, ClamAV required, encryption/rate limits/audit/docs auth. |
| [`docs/deployment/hardened/docker-compose.hardened.yml`](hardened/docker-compose.hardened.yml) | Compose override: uses `PLEXICHAT_CONFIG=/app/config/hardened.yaml`, binds backend to loopback, requires ClamAV, removes public RustFS exposure, mounts hardened nginx template. |
| [`docs/deployment/hardened/nginx.hardened.conf`](hardened/nginx.hardened.conf) | Edge TLS termination + reverse proxy reference (HSTS, security headers, proxy headers, `/admin` edge-allowlist hook, WebSocket/gateway timeouts). Mount **real** certificates. |
| [`docs/deployment/hardened/.env.hardened.example`](hardened/.env.hardened.example) | Secret names/template only — **no real secrets**. Copy to `.env`. |

## 1. Reverse proxy + TLS

**Requirement:** terminate TLS at a trusted reverse proxy; do not expose the backend directly.

1. Provision certificates from an approved CA (or an internal CA consistent with policy). Place `fullchain.pem`/`privkey.pem` where the edge mounts `/etc/nginx/certs` (or use your managed ingress). **Do not rely on self-signed certs in production.**
2. Use `docs/deployment/hardened/nginx.hardened.conf` as the edge reference: HTTPS redirect, TLS 1.2/1.3, HSTS (`max-age=31536000; includeSubDomains` — enable preload only after validation), security headers, and proxying of `/api/`, `/gateway`, `/docs`, `/openapi.json`, `/redoc`, `/admin` to `backend:8000`.
3. Forward `Host`, `X-Real-IP`, `X-Forwarded-For`, and `X-Forwarded-Proto` (the hardened nginx config does). In `config/hardened.yaml`, set `api.trust_x_forwarded_for: true` and `api.trusted_proxies` to **only** your proxy IPs/CIDRs. Never trust arbitrary forwarded headers.
4. Keep `tls.enabled: false` in the backend when the proxy terminates TLS (the default hardened posture). If you terminate TLS at the backend instead, set `tls.enabled: true` with managed `tls.cert_path`/`tls.key_path` and review `static_client`/proxy topology accordingly.
5. WebSocket gateway: clients connect to `wss://<host>/gateway` (not `/ws`, not under `/api/v1`). Ensure the proxy passes `Upgrade`/`Connection` and uses long timeouts (see nginx config). Readiness: `GET /api/v1/ready`; health: `GET /api/v1/health`.

## 2. Strict origins, hosts, and CORS

1. **CORS (API):** set `api.cors_origins` to your exact public origin(s) (e.g., `https://chat.example`). Wildcards are rejected by the API when wildcard CORS is not explicitly allowed (`src/api/config.py`). Keep `api.cors_allow_credentials` only if required; restrict methods/headers to what the client needs.
2. **WebSocket origins:** set `websocket.allowed_origins` to the same exact origin(s).
3. **Admin origins/hosts:** keep `admin_ui.allowed_origins: []` unless cross-origin admin access is explicitly required and reviewed. Keep `admin_ui.host_restriction.enabled: true` and `allowed_hosts` to loopback/explicit management sources.
4. **Config-loader note (important):** `ConfigLoader._deep_merge` **concatenates/deduplicates lists** (defaults + overlay). Verify effective values after merge (command in `config/hardened.yaml` header). The hardened profile also removes the non-loopback admin host from `src/config_defaults.py` so code defaults do not inject an environment-specific IP. Review `api.cors_origins`/`websocket.allowed_origins` effective values for your release and run with explicit, minimal allowlists.

## 3. Admin restricted to loopback / allowlist

Builds on [Admin hardening](../admin-hardening.md) (`src/api/routes/admin/utils.py`, `src/core/admin/__init__.py`).

1. **Application layer:** `admin_ui.host_restriction.enabled: true`, `allowed_hosts: [127.0.0.1, localhost, ::1]` plus explicit management jump-host/proxy IPs only when required. Keep `admin_ui.blocked_ips` current.
2. **Edge layer (defense in depth):** enforce an allowlist for `/admin` at nginx/ingress (see `geo $admin_allowed` hook in `nginx.hardened.conf`) or restrict via VPN/private network. Do not expose `/admin` publicly.
3. **AuthN/AuthZ:** opaque admin sessions, explicit `require_admin_permission` scopes (no wildcards), `require_otp: true`, `force_password_change_first_login: true`, tight session limits/timeouts.
4. **Destructive actions:** `admin_ui.approval_workflows.enabled: true`, `single_admin_bypass: false`, multi-admin approval for `users.force_purge`, `users.delete`, `servers.delete` (tune to policy).
5. **Admin TLS/rate limits:** keep admin behind TLS; keep `admin_ui.rate_limit` and global `rate_limiting.enabled: true`.

## 4. ClamAV required and tuned

1. `media.scanner_enabled: true` in hardened config; server env `SCANNER_ENABLED=true` in the hardened compose override (`src/config_defaults.py`: `media.scanner_enabled/scanner_host/scanner_port`; `src/core/media/_scanning.py`; `src/server/config_loader.py` applies `SCANNER_*` env overrides).
2. Point `SCANNER_HOST`/`SCANNER_PORT` at the `clamav` service (compose network) or your approved scanner endpoint. Do not bypass scanning for user uploads in FI-like use.
3. Keep signature databases fresh (compose includes `clamav` + `clamav-unofficial-sigs`). Monitor freshness and scan failures; treat scanner unavailability per your policy (fail closed for uploads where feasible).
4. Tune limits to your attachment policy and scanner capacity: compose sets `StreamMaxLength`/`MaxScanSize`/`MaxFileSize`/`MaxScanTime`; align with `media.size_limits`/proxy limits and `client_max_body_size` at the edge. Re-validate with realistic files and [load-testing.md](load-testing.md) (ClamAV is a notable RAM/CPU baseline).

## 5. Least-privilege DB / Valkey / RustFS credentials

1. **PostgreSQL:** dedicated application role (not superuser) with privileges only on the application database/schema; `POSTGRES_SSLMODE=require` minimum, prefer `verify-full` with CA material when not on a fully trusted network. Rotate passwords; store in a secret manager/.env (`chmod 600`). Connection settings: `config/hardened.yaml` `database.postgres.*` (env-interpolated).
2. **Valkey:** require `VALKEY_PASSWORD` (compose fails fast if unset); enable TLS (`valkey.ssl` / deployment TLS) when traffic crosses untrusted networks; restrict network access to backend/authorized clients; use a dedicated ACL/user if your Valkey version/policy supports it.
3. **RustFS/S3:** create a **bucket-scoped** access key for the app (no console/admin reuse); keep `RUSTFS_CORS_ALLOWED_ORIGINS` empty unless a separate signed-browser-upload flow is explicitly configured; do not expose RustFS publicly (hardened compose removes published ports). `media.storage_backend: s3`, `encrypt_at_rest: true`.
4. **Network segmentation:** only the edge publishes 80/443; backend bound to loopback in the hardened override; data services internal-only.

## 6. Audit logging and retention

1. `admin_ui.audit.log_to_file: true`, `log_to_database: true`, `sensitive_actions_always_db: true`, `retention_days: 365` (tune to legal/policy). Forward logs to your SIEM; review regularly (`docs/admin-hardening.md`).
2. `authentication.account_deletion.audit_log` and `authentication.dsar.audit_log` provide hash-chained audit options with optional S3 backup — enable/point to approved storage per retention/legal-hold policy.
3. Protect log integrity (append-only storage/WORM where required), restrict access, and define deletion/hold procedures. Backups (restic) are encrypted by `RESTIC_PASSWORD`; use an offsite secondary repo for resilience.

## 7. Secret management guidance

- **Never commit secrets.** `.env` holds DB/Valkey/S3/restic and related secrets: create with `umask 077`, keep `chmod 600` (compose policy).
- **Generate with a CSPRNG:** e.g. `openssl rand -base64 32` or your platform secret manager. Replace every `CHANGE_ME_*` in `.env.hardened.example`.
- **Key material:** `PLEXICHAT_SYSTEM_KEY` / `PLEXICHAT_MESSAGE_KEY` and keyrings under `/home/plexichat/.plexichat` (compose volume). Losing keys makes encrypted data unrecoverable — back up key material securely and separately per policy. Production boot aborts on placeholder/insecure security keys/passwords (`src/server/security_checks.py`).
- **Runtime config rewrite:** the backend image may rewrite its mounted config (e.g., first-boot generated values); mount config writable as compose does, but treat secret-bearing values as env/secret-manager sourced, not hand-edited in git.
- **Prefer a secret manager** (Vault/KMS/SM/ESO) over flat files for production; inject env vars at runtime. Rotate credentials/keys on a schedule and after incidents.

## 8. Measured capacity (reference)

Size using **measured, workload-specific** numbers — not estimates alone. See [Load testing and capacity](load-testing.md) for the scaffold (`plexichat/loadtest/`), scenarios (login/Argon2, idle WS, message send/fanout), how to read the capacity knee with `docker stats`, and **reference-host** component measurements/estimates. Key drivers: Argon2 logins, 1 backend asyncio process by default, per-member permission fanout, encryption + blind-index + write-time search indexing, Valkey/Postgres/RustFS/**ClamAV** baseline RAM. Run `login_load.py`, `ws_idle.py`, `msg_send.py` against your target and re-run `bench_primitives.py` on that host.

## Step-by-step operator runbook

1. **Prepare host/network:** isolate the deployment network; allow inbound 80/443 to the edge only; restrict `/admin` to management networks; plan RTP/UDP `30000-30100` exposure only if WebRTC is required.
2. **Pin and verify supply chain:** set `BACKEND_VERSION`/`CLIENT_VERSION` to released tags; verify image digests/compose SHA per `docker-compose.yml` policy; do not track unverified `dev` compose.
3. **Create secrets:** `cp docs/deployment/hardened/.env.hardened.example .env && (umask 077; chmod 600 .env)`. Fill all required values (CSPRNG). Set real `PLEXICHAT_PUBLIC_SERVER_URL`, passkey RP/origin, scanner, and least-privilege DB/Valkey/S3/backup credentials.
4. **Configure origins/hosts:** edit `config/hardened.yaml` — set `api.cors_origins` and `websocket.allowed_origins` to your exact `https://` origin(s); set `api.trusted_proxies` to proxy IPs/CIDRs; confirm admin allowlists. Verify effective merged config (list-merge note above).
5. **Provision TLS:** install real `fullchain.pem`/`privkey.pem` into the certs volume/mount; confirm `nginx.hardened.conf` `server_name`/OCSP/DNS settings for your CA/baseline.
6. **Start services:** from `plexichat/`, `docker compose -f docker-compose.yml -f docs/deployment/hardened/docker-compose.hardened.yml up -d`. Confirm `db`, `valkey`, `clamav`, `backend` healthy; RustFS internal-only.
7. **Migrations:** auto-migrate is enabled by default; to apply/preview explicitly: `docker compose exec backend python main.py migrate-db [--dry-run]` (see [deployment index](index.md)).
8. **Validate controls:** `GET https://<host>/api/v1/ready` and `/api/v1/health`; confirm HTTPS redirect/HSTS/headers; confirm CORS rejects unlisted origins; confirm `/admin` reachable only from allowlisted sources and requires OTP/permissions; confirm uploads are scanned (ClamAV healthy/fresh); confirm docs auth if `docs.security.require_auth: true`.
9. **Logging/backup:** verify admin/audit logs flowing to SIEM; verify restic schedule/retention and a restore test; protect key material backups.
10. **Capacity:** run the load-test scaffold against this hardened, non-production-equivalent instance and record the capacity knee ([load-testing.md](load-testing.md)).

## NO_STRICT_CONFIG / production checks

- Keep `NO_STRICT_CONFIG=false` (hardened compose sets it). Do not bypass strict/production safety checks.
- `application.environment: production` enables production enforcement; `src/server/security_checks.py` aborts production boot on default/placeholder security keys/passwords (e.g., `media.signing_key`, weak DB/Valkey/turn secrets, message-key warnings) and on other insecure defaults. Resolve warnings by supplying real secrets/config — not by downgrading environment.
- CLI validates config and exits on failure unless `NO_STRICT_CONFIG=true` (development-only escape hatch; see `src/cli/main.py`/README). Do not use in hardened/FI deployments.

## Validation performed for this profile

- Config keys in `config/hardened.yaml` were checked against `src/config_defaults.py`, `src/config_defaults_authentication.py`, and `src/config_defaults_artifacts.py` (no unknown keys).
- Overlay is valid YAML and contains **no real secrets** (env placeholders / example domains only).
- Effective-merge behavior and list-concatenation risk are documented; `src/config_defaults.py` hardened to remove a non-loopback admin `allowed_hosts` entry.
- Runtime integration/load tests were **not** executed in this task; operators must validate in their environment (health checks, scanning, CORS/admin restrictions, backups, and load tests).
