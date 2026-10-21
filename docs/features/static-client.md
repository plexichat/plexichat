# Static client serving

Serve the official web client (`plexichat-client` Vite build) directly from the FastAPI backend. When you enable this feature, the backend downloads releases from GitLab, verifies the SHA-256 hash, and serves the files with correct cache headers, security headers, and rate limiting. You don't need a separate Nginx deployment for the client.

## When to use this feature

*   **Single deployment surface.** Run one container, one domain, and one CORS configuration.
*   **Automatic updates.** When you publish a new client tag, the backend picks it up on the next check. The delay is controlled by `auto_update_min_age_seconds`.
*   **Integrity verification.** The backend verifies every `dist.zip` against the `dist.zip.sha256` sidecar that GitLab publishes alongside the release. A mismatch aborts the install.
*   **Performance.** Hashed assets receive `Cache-Control: public, max-age=31536000, immutable`, ETag and `304 Not Modified` handling, and full HTTP Range support.

## How it works

1.  On startup, the backend reads the `static_client` configuration and the server version.
2.  It selects the most recent release that matches the `version_pin` policy.
3.  It downloads `dist.zip` and `dist.zip.sha256` from the GitLab Releases API.
4.  It verifies the SHA-256 hash. If the hash doesn't match, the install stops.
5.  It unpacks the zip into `<install_dir>/<version>/` and writes `current_version` with the active version string.
6.  It writes `config.js` to the dist root with the detected server URL and version.
7.  The static client middleware, the outermost ASGI layer, serves the active version for all non-API paths. The middleware applies ETag and Range handling, security headers, rate limiting, and SPA fallback.

The version pin policies are defined in `src/config_defaults.py:848` and `src/static_client/config.py:102`:

*   `match_server` (canonical default): Install the highest build with the same stage, major version, and minor version as the server. This is the safest option.
*   `latest`: Install the latest release for the same stage and major version, regardless of minor version.
*   Explicit version such as `a.1.0-84`: Pin to that exact release.

## File layout

```text
~/.plexichat/client/
├── current_version          # Single-line file with the active version string
├── a.1.0-145/               # Unpacked dist for this version
│   ├── index.html
│   ├── app.html
│   ├── config.js            # Written by the backend after install
│   ├── favicon.svg
│   └── assets/
└── ...
```

The install directory defaults to `~/.plexichat/client` (`src/config_defaults.py:846`). The loader in `src/static_client/config.py:120` expands `~` and falls back to that path when the configured value is not absolute. The backend retains only the active version and prunes older installs.

## Configuration

For the complete reference, see [Static client configuration](../deployment/configuration/config-static-client.md). The minimum configuration to enable the feature is:

```yaml
static_client:
  enabled: true
  git_lab:
    project_id: 2
```

Set the GitLab token in the environment before you start the server:

```bash
export PLEXICHAT_GITLAB_TOKEN="glpat-xxxxxxxxxxxxxxxxxxxx"
```

The loader reads the token with `os.environ.get(private_token_env)` (`src/static_client/config.py:28`). The default environment variable is `PLEXICHAT_GITLAB_TOKEN`.

Key settings:

| Key | Default | Description |
| --- | --- | --- |
| `enabled` | `false` | Master switch. Set to `true` to fetch releases and install middleware. |
| `serve` | `true` | Serve files when enabled. Set to `false` to manage installs without serving. |
| `install_dir` | `~/.plexichat/client` | Directory where releases are unpacked. |
| `version_pin` | `match_server` | Version selection policy. |
| `auto_update` | `false` | When `true`, a background task checks for new releases at `auto_update_check_interval_seconds`. |
| `auto_update_min_age_seconds` | `3600` | Minimum age of a release before the backend installs it. |
| `auto_update_check_interval_seconds` | `3600` | Interval between update checks. |

The typed config in `src/static_client/config.py:103` defaults `auto_update` to `true` when the key is absent. The canonical default in `src/config_defaults.py:849` is `false`. Declare the value explicitly in `config.yaml` to avoid ambiguity.

## Cache-Control

The backend sends different `Cache-Control` values by content type (`src/static_client/config.py:34`, `src/config_defaults.py:859`):

| Content type | Header value |
| --- | --- |
| Hashed assets (`/assets/*` with a content hash) | `public, max-age=31536000, immutable` |
| HTML entry points | `no-store, max-age=0` |
| Everything else | `public, max-age=300` |

## Security headers

The backend adds the following headers to every static response (`src/static_client/config.py:42`, `src/config_defaults.py:864`):

| Header | Value |
| --- | --- |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `SAMEORIGIN` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `geolocation=(), microphone=(self), camera=()` |
| `Content-Security-Policy` | Configurable. The default in `src/config_defaults.py:869` allows `self`, `https://cdn.jsdelivr.net`, and `https://fonts.googleapis.com`. |

The middleware also strips the `Authorization` header from the scope before it serves files, so a stray `Authorization: Bearer ...` header on a page request doesn't leak into the response chain.

## Rate limiting

Two rate-limiting layers protect static routes. Both are per-IP.

### Layer 1: Global rate limiter (`rate_limiting.routes`)

Defined in `src/config_defaults.py:291`:

| Route | Requests per 60 s | Burst | Hourly limit |
| --- | --- | --- | --- |
| `static_client_html` (HTML pages and SPA fallback) | 300 | 100 | 6000 |
| `static_client_assets` (JS, CSS, images, `config.js`) | 5000 | 1000 | 180000 |

Configure these limits under `rate_limiting.routes.static_client_html` and `rate_limiting.routes.static_client_assets` in `config.yaml`.

### Layer 2: Static client middleware (`static_client.rate_limit`)

Defined in `src/config_defaults.py:871` and `src/static_client/config.py:70`:

| Tier | Requests per 60 s | Burst |
| --- | --- | --- |
| `html` | 300 | 60 |
| `assets` | 1200 | 120 |

Configure this layer under `static_client.rate_limit.html` and `static_client.rate_limit.assets`. Set `static_client.rate_limit.enabled: false` to disable only this middleware layer without affecting the global limiter. Requests from `127.0.0.1` or `::1` with a valid `X-Plexichat-Internal-Secret` header bypass both layers.

## SPA fallback

The middleware serves the SPA fallback when all of the following are true:

*   The path doesn't match an existing static file.
*   The path isn't under an API prefix (`/api/v1`, `/admin`, `/docs`, `/redoc`, `/openapi.json`).
*   The path isn't under a static asset prefix (`/assets/`, `/js/`, `/css/`).

Behavior:

*   If the path starts with a prefix in `spa_routes`, the backend serves the mapped HTML file. For example, `/app` serves `app.html` and `/settings` serves `settings.html`. The loader sorts routes longest-prefix-first and normalizes prefixes to start with `/` (`src/static_client/config.py:127`).
*   For any other unknown path, the backend serves `index.html` so the client-side router handles navigation.
*   When `invite_redirect` is `true` (the default), a request to `/invite/{code}` returns `302` to `/app.html?invite={code}`.

Default `spa_routes` (`src/config_defaults.py:877`):

```yaml
spa_routes:
  "/app": "app.html"
  "/settings": "settings.html"
  "/register": "register.html"
  "/forgot-password": "forgot-password.html"
  "/reset-password": "reset-password.html"
  "/oauth-callback": "oauth-callback.html"
  "/error": "error.html"
  "/invite": "app.html"
```

## Config injection

After each install, the backend writes a runtime `config.js` to the distribution root (`src/config_defaults.py:888`):

```yaml
static_client:
  config_injection:
    enabled: true
    filename: "config.js"
    public_server_url: ""
    content: 'window.PLEXICHAT_CONFIG = { serverUrl: "{origin}", hideServerField: true, defaultTheme: "ocean", version: "{version}" };'
```

*   When `public_server_url` is empty, the backend derives `serverUrl` from the request `Host` or `Origin` header. The same build works on any hostname.
*   Set `public_server_url` only when a proxy rewrites `Host` and you need to pin a specific origin.
*   In Docker, set `PLEXICHAT_PUBLIC_SERVER_URL` (for example, `https://chat.example.com`). The loader in `src/static_client/config.py:195` prefers this environment variable over the YAML value.
*   Placeholders `{origin}` and `{version}` are replaced at install time.
*   Set `enabled: false` if you ship your own `config.js`.

## GitLab source

The backend pulls releases from the GitLab Releases API (`src/config_defaults.py:852`):

```yaml
static_client:
  git_lab:
    project_id: 2
    api_url: "https://gitlab.plexichat.com/api/v4"
    private_token_env: "PLEXICHAT_GITLAB_TOKEN"
    verify_tls: true
    request_timeout_seconds: 30
```

Create a GitLab access token with the `read_api` scope and set it in the environment before you start the server.

## Limits and logging

| Key | Default | Description |
| --- | --- | --- |
| `max_zip_size_bytes` | `104857600` (100 MiB) | The backend rejects the download if `Content-Length` exceeds this value and caps the in-memory buffer to the same size. |
| `log_downloads` | `false` | Set to `true` to log each successful install with its version and path. |

## CI integration

The `plexichat-client` GitLab CI publishes `dist.zip.sha256` alongside `dist.zip` in the release assets. The backend fetches both files and rejects the install when the hashes don't match.

## Disable the feature

To run Plexichat without the bundled client, for example behind a separate Nginx, set:

```yaml
static_client:
  enabled: false
```

When `enabled` is `false`, the middleware is not installed, no fetches run, and the fallback routes are not registered.
