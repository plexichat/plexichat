# Admin routes

This package contains administrative API endpoints mounted at
`/api/v1/admin`. The `/admin` path prefix is configurable through
`admin_ui.path` in `config.yaml`. All endpoints require elevated
permissions and are audited.

## Overview

Admin routes handle server management, moderation, user administration,
and system configuration. The package is assembled in
`src/api/routes/admin/__init__.py`, which includes each sub-router into a
single `APIRouter` that `src.api.routes.create_api_router()` mounts at the
configured admin path. Common error responses (`400`, `401`, `403`) are
defined once on the parent router.

## Route modules

| Module | Description |
|--------|-------------|
| `auth.py` | Admin login, session lifecycle, and token validation for the admin dashboard |
| `audit.py` | Audit log search, filtering, export (CSV, JSON), retention, and compliance reporting |
| `approvals.py` | Approval queue, approve or reject actions, and escalation rules |
| `artifacts.py` | Admin view and management of artifact content |
| `bots.py` | Bot account listing, creation, update, deletion, permission scoping, and rate limit configuration |
| `capabilities.py` | Management of advertised server capabilities |
| `dashboard.py` | System overview: server statistics, active users, message counts, storage usage, health indicators, and recent activity |
| `data_export.py` | User data export generation and download |
| `database.py` | Manual backup and restore triggers, `VACUUM` and `ANALYZE`, storage statistics, and replication status |
| `licensing.py` | License key validation, activation, expiry tracking, and feature flag management |
| `logs.py` | System log viewer with level filtering (`debug`, `info`, `warn`, `error`), time-range and source filtering, export, and rotation |
| `migrations.py` | Migration status and history, running pending migrations, rollback, and integrity validation |
| `moderation.py` | Warnings, timeouts, bans and unbans, moderation history, appeals, and bulk actions |
| `plexijoin.py` | Federation connection management, status, and request approval |
| `reindex.py` | Search index rebuild, status, statistics, and scheduled maintenance |
| `roles.py` | Role create, update, and delete; permission sets; assignment; and inheritance |
| `security.py` | Password policy, two-factor enforcement levels, IP blocking, rate limiting, and session timeout configuration |
| `telemetry.py` | Viewing and configuring collected telemetry, retention settings, and report export |
| `tickets.py` | Support ticket listing, assignment, and status or priority management |
| `ui.py` | Admin UI customization: branding, theming, announcements, login page, and footer or legal links |
| `utils.py` | Health checks, cache invalidation, system information, and maintenance mode toggle |
| `users/` | User administration sub-package (see `users/README.md`) |

## Authentication

Most admin routes use the same bearer and cookie authentication as the
main API, with additional authorization checks (admin role or session).
The admin login flow in `auth.py` manages its own session table
(`admin_sessions`) and sets `request.state.admin_id` for subsequent
requests. The rate limiter recognizes `request.state.admin_id` as an admin
bypass even when `request.state.user` is not set.

## Authorization and auditing

- Write operations typically require a completed two-factor verification
  and an IP allowlist check when configured.
- State-changing operations are audit-logged. You can query and export
  these entries through `audit.py`.
- Bulk operations and sensitive actions have stricter rate limits than
  read operations.

## Rate limiting

Rate limits for admin endpoints are enforced by the shared
`src.core.ratelimit` engine. The exact limits depend on server
configuration. Typical defaults are:

| Operation type | Limit |
|----------------|-------|
| Read | 100 requests per minute |
| Write | 30 requests per minute |
| Bulk | 5 requests per minute |

Admin sessions identified by `request.state.admin_id` bypass per-user
limits when the rate limiter detects an admin principal.

## Add a new admin route

1. Create a new module in this directory (for example, `widgets.py`) that
   defines `router = APIRouter()`.
2. Add handlers to the router with standard FastAPI decorators and Pydantic
   response models.
3. Include the router in `src/api/routes/admin/__init__.py`:

   ```python
   from .widgets import router as widgets_router
   router.include_router(widgets_router)
   ```

4. If the route needs new schemas, add them under `src.api.schemas.admin`.
5. Add authorization checks (admin role, two-factor, or IP allowlist) as
   required by the operation.

## Related documentation

- `../README.md` — full route table for non-admin endpoints
- `users/README.md` — user administration sub-package and its mixin
  architecture
- `../../middleware/README.md` — authentication, rate limiting, and error
  handling
- `../../../core/admin` — underlying admin business logic
