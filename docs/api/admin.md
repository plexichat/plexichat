# Admin API Surface

The backend exposes an operator-only admin surface in addition to the public API.

## Base Path

All paths below are relative to the configured admin base path, shown here as `<admin-base>`. The default value is `/admin`, and these routes are mounted at the application root rather than under `https://api.plexichat.com`.

## Access Model

- admin auth and most JSON routes require administrator credentials or an admin session token
- host restrictions are enforced before admin handlers run
- some UI routes are intentionally excluded from the OpenAPI schema

## Route Groups

### Authentication And Session Management

- `POST <admin-base>/login`
- `POST <admin-base>/verify-otp`
- `POST <admin-base>/logout`
- `POST <admin-base>/auth/change-password`
- `GET <admin-base>/auth/security-status`
- `POST <admin-base>/auth/2fa/begin-setup`
- `POST <admin-base>/auth/2fa/disable`
- `POST <admin-base>/auth/2fa/regenerate-backup-codes`

### Dashboard, Logs, And Database Health

- `GET <admin-base>/dashboard`
- `GET <admin-base>/logs`
- `GET <admin-base>/logs/{filename}`
- `GET <admin-base>/database/pool-health`

### Ticket And User Operations

- `GET <admin-base>/tickets`
- `GET <admin-base>/tickets/{ticket_id}`
- `PATCH <admin-base>/tickets/{ticket_id}/status`
- `GET <admin-base>/tickets/{ticket_id}/notes`
- `POST <admin-base>/tickets/{ticket_id}/notes`
- `GET <admin-base>/users/search`
- `GET <admin-base>/users/{user_id}`
- `PUT <admin-base>/users/{user_id}/tier`
- `POST <admin-base>/users/{user_id}/badges/{badge}`
- `DELETE <admin-base>/users/{user_id}/badges/{badge}`
- `GET <admin-base>/users/{user_id}/notes`
- `POST <admin-base>/users/{user_id}/notes`
- `POST <admin-base>/users/{user_id}/force-username-change`

### Security Controls

- `GET <admin-base>/security/blocked-ips`
- `POST <admin-base>/security/block-ip`
- `DELETE <admin-base>/security/unblock-ip/{ip_address}`
- `GET <admin-base>/security/access-tokens`
- `POST <admin-base>/security/access-tokens`
- `GET <admin-base>/security/access-tokens/{token_id}`
- `PATCH <admin-base>/security/access-tokens/{token_id}`
- `POST <admin-base>/security/access-tokens/{token_id}/rotate`
- `POST <admin-base>/security/access-tokens/{token_id}/scopes`
- `DELETE <admin-base>/security/access-tokens/{token_id}/scopes/{scope_id}`
- `POST <admin-base>/security/access-tokens/{token_id}/revoke`
- `GET <admin-base>/security/banned-usernames`
- `POST <admin-base>/security/banned-usernames`
- `DELETE <admin-base>/security/banned-usernames/{pattern_id}`
- `POST <admin-base>/security/force-logout`
- `POST <admin-base>/security/lock-user`
- `POST <admin-base>/security/unlock-user`
- `POST <admin-base>/security/logout-all`

### Moderation And AutoMod

- `GET <admin-base>/hash-reports`
- `GET <admin-base>/hash-reports/counts`
- `POST <admin-base>/hash-reports/{report_id}/review`
- `GET <admin-base>/message-reports`
- `GET <admin-base>/message-reports/counts`
- `POST <admin-base>/message-reports/{report_id}/review`
- `GET <admin-base>/user-reports`
- `GET <admin-base>/user-reports/counts`
- `POST <admin-base>/user-reports/{report_id}/review`
- `GET <admin-base>/blocked-hashes`
- `POST <admin-base>/blocked-hashes`
- `DELETE <admin-base>/blocked-hashes/{hash_value}`
- `GET <admin-base>/blocked-users`
- `POST <admin-base>/blocked-users`
- `DELETE <admin-base>/blocked-users/{user_id}`
- `GET <admin-base>/automod/rules`
- `POST <admin-base>/automod/rules`
- `PATCH <admin-base>/automod/rules/{rule_id}`
- `DELETE <admin-base>/automod/rules/{rule_id}`
- `GET <admin-base>/automod/config`
- `PUT <admin-base>/automod/config`

### Admin Telemetry And UI

- `GET <admin-base>/telemetry/stats`
- `GET <admin-base>/telemetry/history`
- `POST <admin-base>/telemetry/reset`
- `GET <admin-base>/telemetry/export`
- `GET <admin-base>/`
- `GET <admin-base>/login`
- `GET <admin-base>/ui`
- `GET <admin-base>/ui-dashboard`

## Notes

- the admin surface is operational and human/operator focused, not a public third-party integration surface
- UI routes serve HTML and redirect helpers, while the rest of the routes return JSON responses
- if `admin_ui.enabled` is false, the admin router is not mounted at runtime