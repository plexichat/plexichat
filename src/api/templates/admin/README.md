# Admin templates

Provide the server-rendered HTML, CSS, and JavaScript for the Plexichat admin dashboard. The API server serves these files alongside the JSON API under `/api/v1/admin/`.

## Files

| File | Purpose |
|------|---------|
| `login.html` | Admin login page |
| `dashboard.html` | Single-page admin dashboard |
| `dashboard.css` | Dashboard styles |
| `dashboard.js` | Dashboard client logic |
| `migrations.html` | Migration management page |

### `login.html`

Admin sign-in page. The page contains:

* Username and password form that posts to `POST /api/v1/admin/login`.
* One-time 2FA setup flow that displays a QR code (`/api/v1/qr?size=200x200&data=<otp_qr_uri>`) and the raw `otp_secret` when the response is `otp_setup_required`.
* 2FA verification form that posts to `POST /api/v1/admin/verify-otp` with `admin_id`, `code`, `is_setup`, and `challenge_token` when the response is `otp_required`.
* Error display for `detail.error.message` and JavaScript error fallback.
* Session check on load: if `sessionStorage.plexichat-admin-token` exists, the page fetches `GET /api/v1/admin/dashboard` and redirects to `/api/v1/admin/ui-dashboard` when the token is valid.

On success, the page stores the token as `sessionStorage.plexichat-admin-token` and navigates to `/api/v1/admin/ui-dashboard`. The inline `<style>` and `<script>` tags use `nonce="{{ csp_nonce }}"`.

### `dashboard.html`

Single-page admin UI with tab navigation. The template includes:

* **Sidebar** — navigation for Tabs: `Dashboard`, `Users`, `Admins`, `Tickets`, `Moderation`, `Security`, `Telemetry`, `Logs`, `Database`, `Approvals`, `Audit Log`, `AutoMod`, `Bots`, `Artifacts`, `Migrations`, `Alerts`, plus system tabs `PlexiJoin` and `License`, and `My Account`. Also shows the API version and a collapse toggle.
* **Header** — page title, health badge (`System Healthy`), global search, notifications dropdown, docs link, and admin avatar.
* **Dashboard tab** — health badge, last-updated timestamp, time-range selector (`1h`, `6h`, `24h`, `7d`), stat cards (Active Users 24h, Total Registered, Scheduled Deletions, Avg API Latency, Error Rate), charts (`chart-tps`, `chart-latency`, `chart-db-pool`, `chart-sys-res`, `chart-errors`, `chart-status-dist`, `chart-telemetry-history`), resource cards (CPU, Memory, Disk, Uptime), worker-health card (worker ID, status, backoff, poll count, running; tuning constants and audit log), endpoint performance table, and telemetry history controls (endpoint, method, hours, bucket).
* **Users tab** — inner tabs `All Users`, `Scheduled Deletions`, `Tier Catalog`; user search, paginated user table, detail panel, and deletions table.
* **Admins, Tickets, Moderation, Security, Telemetry, Logs, Database, Approvals, Audit, AutoMod, Bots, Artifacts, Migrations, Alerts, PlexiJoin, License, Account tabs** — tables and forms that call the corresponding `GET/POST/PATCH/DELETE /api/v1/admin/*` JSON endpoints.

The page loads `Chart.js` from `https://cdn.jsdelivr.net/npm/chart.js` with an integrity hash and the CSP nonce, and loads `/api/v1/admin/static/dashboard.css`.

### `dashboard.css`

Dashboard styling:

* Admin color scheme distinct from the main app (OKLCH-based CSS variables: `--bg`, `--foreground`, `--card`, `--primary`, etc.).
* Responsive grid for stat cards, metric tiles, and resource cards.
* Table, card, sidebar, header, and badge styles.
* Chart container sizing and dark-mode support.
* Scrollbar styling.

Referenced as `<link rel="stylesheet" href="/api/v1/admin/static/dashboard.css" nonce="{{ csp_nonce }}">`.

### `dashboard.js`

Client-side interactivity for the dashboard. The script handles:

* Tab and inner-tab switching (`data-tab`, `data-inner-tab`, `data-click` handlers such as `toggleSidebar`, `openExport`, `resetTelemetry`, `refreshTelemetryHistory`).
* Fetching JSON data from `GET /api/v1/admin/dashboard`, `/api/v1/admin/telemetry`, `/api/v1/health`, `/api/v1/ready`, and domain-specific admin endpoints, using `Authorization: Bearer <token>` from `sessionStorage`.
* Rendering stats, resource cards, and endpoint performance tables; filtering endpoints and selecting time ranges (`metric-hours`, `endpoint-filter`).
* Rendering charts with `Chart.js` for TPS, latency, DB pool, system resources, error rate, status distribution, and telemetry history.
* Worker-health polling, tuning-constant edits (`tuning-poll-interval`, `tuning-heartbeat`, `tuning-max-backoff`, `tuning-reset-polls`, `tuning-init-backoff`, `tuning-glide-timeout`, `tuning-heartbeat-alert`), and system alerts.
* Auto-refresh polling for stats and history.

### `migrations.html`

Migration management page:

* Migration status table with columns: version, name, checksum, status, and applied date.
* "Run pending migrations" button that triggers the migration run endpoint.
* Rollback controls for reverting the last migration.
* Integrity check results display showing checksum mismatches.

## Security

* All inline styles and scripts use `nonce="{{ csp_nonce }}"` so a strict `Content-Security-Policy` can allow them.
* Tokens are stored only in `sessionStorage` (not `localStorage`) and are sent as `Authorization: Bearer` headers. The login page clears the token when `GET /api/v1/admin/dashboard` returns `401`.
* QR code images are fetched from the internal `/api/v1/qr` endpoint, not an external service.

## Health and rate limits

* The dashboard displays health from `GET /health` (advisory `events_metrics`, `cross_worker_listener`, `is_queue_recreate_pending`) and `GET /ready` (liveness). These are REST endpoints in `src/api/routes/health.py`, not template features.
* Rate limits such as the DSAR `1 request per 24h` (`429`) are enforced by the API and surfaced as error toasts in the dashboard JS. Templates do not enforce rate limits themselves.
