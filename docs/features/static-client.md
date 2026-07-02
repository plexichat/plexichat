# Static Client Serving

Plexichat can serve the official web client (the `plexichat-client` Vite
build output) directly from the FastAPI backend, with no separate Nginx
deployment required. The backend downloads releases from GitLab, verifies
them with SHA256, and serves them with the right cache, security, and rate
limit headers.

## Why

* **Single deployment surface**: one container, one domain, one CORS config.
* **Auto-update**: when a new `a.X.Y-Z` tag is published for the client, the
  backend picks it up on the next check (subject to `auto_update_min_age_seconds`).
* **Integrity**: every dist is verified against a SHA256 file published
  alongside the zip in the GitLab release.
* **Performance**: hashed assets get `Cache-Control: public, max-age=31536000, immutable`,
  ETag/304 short-circuiting, and full Range support.

## How it works

1. On startup, the backend reads its `static_client` config and the running
   server's version.
2. It looks up the most recent release of the same stage, major, and minor
   version as the server (configurable via `version_pin`).
3. It downloads `dist.zip` and `dist.zip.sha256` from the GitLab release.
4. It verifies the zip's SHA256 against the sidecar file. Mismatches abort
   the install.
5. It unpacks the zip into `<install_dir>/<version>/` and writes a marker
   file `current_version` containing the active version string.
6. It writes a `config.js` into the dist root with the detected server URL
   and version.
7. The static client middleware (the outermost ASGI layer) serves the
   active version for all non-API paths, with ETag/Range, security headers,
   rate limiting, and SPA fallback.

## File layout

```
~/.plexichat/client/
├── current_version          # single-line file with the active version string
├── a.1.0-59/                # unpacked dist (one directory per version)
│   ├── index.html
│   ├── app.html
│   ├── config.js            # written by the backend
│   ├── favicon.svg
│   └── ...
└── a.1.0-59/                # newest, marked current_version
    └── ...
```

Old versions are pruned automatically; only the currently-active version is
retained.

## Configuration

See [config-static-client.md](configuration/config-static-client.md) for the
full configuration reference. The minimum required to enable the feature is:

```yaml
static_client:
  enabled: true
  git_lab:
    project_id: 42
  # and PLEXICHAT_GITLAB_TOKEN set in the environment
```

## Security headers

Every static response is sent with:

| Header | Value |
| --- | --- |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `SAMEORIGIN` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `geolocation=(), microphone=(self), camera=()` |
| `Content-Security-Policy` | configurable, default allows the Plexichat client's known sources |

The `Authorization` header is also stripped from the scope before files are
served, so a stray `Authorization: Bearer ...` on a page request never
leaks into the response chain.

## Rate limiting

Per-IP, shared with the rest of the API:

| Route | Default window | Default burst | Hourly cap |
| --- | --- | --- | --- |
| `static_client:html` (HTML pages + SPA fallback) | 30 / 60s | 10 | 600 |
| `static_client:assets` (JS/CSS/images/favicons/config.js) | 600 / 60s | 100 | 18000 |

Limits are tunable under `rate_limiting.routes.static_client_*` in
`config.yaml`.

## SPA fallback

Any path that:

* is not an exact match for a known static file
* is not under an API prefix (`/api/v1`, `/admin`, `/docs/api`, ...)
* is not under a static asset prefix (`/assets/`, `/js/`, `/css/`, ...)

is served `index.html` so the client-side router can take over. The
configured `spa_routes` map URL prefixes to specific HTML files (e.g. `/app`
→ `app.html`, `/settings` → `settings.html`).

`/invite/{code}` is 302-redirected to `/app.html?invite={code}` so invite
links land in the SPA with the code already filled in.

## CI integration

The `plexichat-client` repository's GitLab CI now also publishes
`dist.zip.sha256` next to `dist.zip` in the release assets. The backend
fetches both and rejects installs where the hashes don't match.

## Disabling

To run Plexichat without the bundled static client (e.g. behind a separate
Nginx), set:

```yaml
static_client:
  enabled: false
```

The middleware is then never installed, no fetches run, and the existing
fallback routes (which only exist when the feature is on) are not registered.
