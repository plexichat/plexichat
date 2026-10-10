# Harden the admin surface

This guide describes how to lock down the admin UI and API. Apply these settings before you expose the server beyond `localhost`.

## Admin authentication

* Admin routes use opaque sessions, not JWTs. Every state-changing or sensitive read operation requires the matching `require_admin_permission` scope (`src/api/routes/admin/utils.py:164`). Scopes are defined in `src/core/admin/permissions.py:42`.
* Wildcard permissions such as `*` are rejected (`src/core/servers/permission_utils.py:28`). Grant each permission explicitly.
* Require OTP for the admin UI:

  ```yaml
  admin_ui:
    require_otp: true
    force_password_change_first_login: true
    session_timeout_minutes: 480
    max_concurrent_sessions: 3
  ```

## Network restrictions

Restrict the admin UI to loopback by default and add explicit allow-lists only when needed:

```yaml
admin_ui:
  host_restriction:
    enabled: true
    allowed_hosts: ["127.0.0.1", "localhost", "::1"]
  allowed_origins: []
  blocked_ips: []
```

* Keep `tls.enabled: false` only for local development. For production, set `tls.enabled: true` and configure `tls.cert_path` and `tls.key_path` (`src/config_defaults.py:621`).
* The server binds to `127.0.0.1:8000` by default (`server.host` in `src/config_defaults.py:70`). Put a reverse proxy in front of it and forward only trusted `X-Forwarded-For` values.

## Rate limiting and approvals

* Global rate limits default to `25000` requests per `60` s, user `10000`, and IP `5000` (`src/config_defaults.py:272`). Keep `rate_limiting.enabled: true` in production.
* Gate destructive actions with the approval workflow:

  ```yaml
  admin_ui:
    approval_workflows:
      enabled: true
      approval_required_admins: 2
      approval_timeout_hours: 48
      require_approval_for:
        - users.force_purge
        - users.delete
        - servers.delete
  ```

  Requests that need approval return `202` until a second admin approves them.

## Sensitive data handling

* Treat `%`, `_`, and `\` literally in search queries. Do not interpret them as wildcards.
* Scheduled-deletion timestamps are in milliseconds. Admin database adapters may return mapping or tuple rows — handle both shapes.
* The API never returns filesystem paths, SQL errors, or raw exception text. Internal errors are logged server-side and surfaced as generic messages.
* Audit-sink failures are emitted through structured logging, not `stdout`.
* Reindex status uses the same permission gate as reindex execution, and its in-progress claim is lock-protected.

## Audit and retention

```yaml
admin_ui:
  audit:
    log_to_file: true
    log_to_database: true
    sensitive_actions_always_db: true
    retention_days: 365
```

Review audit logs regularly and forward them to your SIEM.

## Voice and media

* The SFU backend defaults to `aiortc` (`voice.sfu_backend` in `src/config_defaults.py:396`). Change it only if you operate `mediasoup` or `janus`.
* File uploads are scanned when `media.scanner_enabled` is true and `SCANNER_HOST` points at the `clamav` service (`docker-compose.yml:171`).
* External media goes through `GET /api/v1/media/proxy?url=...` (`src/api/routes/media.py:465`) with SSRF validation. Do not expose RustFS directly.

## Gateway and health

* The WebSocket gateway is `ws://host/gateway` (`src/api/websocket/gateway.py:261`), not `/ws`.
* Use `GET /api/v1/ready` for liveness and `GET /api/v1/health` for detailed checks (`src/api/routes/health.py:18`). Both live under the API prefix (`src/config_defaults.py:130`).

## Next steps

1. Set `PLEXICHAT_SYSTEM_KEY` or protect `~/.plexichat` key material — losing it makes encrypted data unrecoverable.
2. Configure the `backup` service for daily restic snapshots (see `docker/README.md`).
3. Review `docs/security.md` and `docs/configuration.md` for the full option reference.
