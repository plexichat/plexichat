# API templates

Render server-side HTML for the Plexichat web layer. The API server serves these templates alongside the JSON API.

## Structure

| Directory | Purpose |
|-----------|---------|
| `admin/` | Admin dashboard HTML templates, styles, and client scripts |

The `admin/` directory contains the complete admin UI. See `admin/README.md` for file details.

## How templates are served

The API server renders admin templates with Jinja2 (or equivalent) and injects a per-request Content Security Policy nonce (`csp_nonce`). Static assets such as `dashboard.css` are served under `/api/v1/admin/static/`.

* `GET /api/v1/admin/login` — returns `admin/login.html`.
* `GET /api/v1/admin/ui-dashboard` — returns `admin/dashboard.html` after admin authentication.
* `GET /api/v1/admin/static/dashboard.css` — dashboard stylesheet.

Admin authentication for these pages uses the same token as the admin JSON API (`Authorization: Bearer <token>` stored in `sessionStorage` by `login.html`). The dashboard JavaScript fetches JSON endpoints such as `/api/v1/admin/dashboard`, `/api/v1/admin/telemetry`, and `/api/v1/health` to populate stats and charts.

## Health and gateway notes

* **Health** — The templates display health status from `GET /health` (detailed advisory metrics) and `GET /ready` (liveness). The dashboard's "System Healthy" badge and worker-health card poll these endpoints. Templates do not define the health schema.
* **Gateway** — Real-time updates in the dashboard (stat polling, chart refresh, cross-worker events) use `fetch` polling and `Chart.js`. WebSocket gateway events such as `emit_user_update` are server-side only and are not rendered by these templates.

## Add a template

1. Add the HTML file under the appropriate subdirectory (for admin pages, `admin/`).
2. Reference static assets with the `/api/v1/admin/static/` prefix and include `nonce="{{ csp_nonce }}"` on inline `<style>` and `<script>` tags.
3. Register the route that renders the template in `src/api/routes/admin/` or `src/api/templates/` as appropriate.
