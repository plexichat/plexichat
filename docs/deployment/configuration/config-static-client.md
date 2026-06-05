# Static Client Configuration

Plexichat can optionally serve the web client (the `plexichat-client` Vite
build) directly from the FastAPI backend, downloading the latest matching
release from GitLab and verifying it with SHA256. This eliminates the need
for a separate Nginx deployment for the client.

## Configuration Location

Add these settings to your `config.yaml` under the `static_client:` key.

## Core Settings

### Enable / Disable

```yaml
static_client:
  enabled: true           # Master enable
  serve: true             # Whether to actually serve files (vs only manage installs)
  install_dir: "~/.plexichat/client"  # Where dists are unpacked
```

When `enabled` is `false`, the backend will not attempt to fetch releases
and the static client middleware will not be installed. When `serve` is
`false`, the backend may still manage installs in the background (useful
for warming the cache) but will not serve files.

### Version Pin

```yaml
static_client:
  version_pin: "match_server"   # default
```

`version_pin` controls which release the backend tries to install:

- `"match_server"` (default): install the highest build of the same stage
  (alpha/beta/release), same major and same minor version as the running
  server. This is the safe default.
- `"latest"`: install the latest release of the same stage and major
  version as the server, regardless of minor/build.
- Explicit version string (e.g. `"a.1.0-57"`): pin to that exact version.

## Update Behaviour

```yaml
static_client:
  auto_update: false                      # Background auto-update on/off
  auto_update_min_age_seconds: 3600      # Skip releases younger than this
  auto_update_check_interval_seconds: 3600  # Background check cadence
```

When `auto_update` is enabled, a background task runs every
`auto_update_check_interval_seconds` and re-evaluates the desired release
target. Newer releases are only installed once they are at least
`auto_update_min_age_seconds` old, to give downstream caches a chance to
warm and to avoid race conditions with the just-published tag.

Old installs are pruned automatically (only the currently-active version is
retained).

## GitLab Source

The backend pulls releases from the Plexichat GitLab Releases API.

```yaml
static_client:
  git_lab:
    project_id: 123                       # GitLab project ID for plexichat-client
    api_url: "https://gitlab.plexichat.com/api/v4"
    private_token_env: "PLEXICHAT_GITLAB_TOKEN"  # Name of the env var holding the token
    verify_tls: true
    request_timeout_seconds: 30
```

Generate a GitLab access token with `read_api` scope, then set the env var:

```bash
export PLEXICHAT_GITLAB_TOKEN="glpat-xxxxxxxxxxxxxxxxxxxx"
```

## Cache-Control

Different content types get different cache headers:

```yaml
static_client:
  cache_control:
    hashed_assets: "public, max-age=31536000, immutable"  # /assets/*, hashed JS/CSS
    html:         "no-store, max-age=0"                    # *.html (no cache)
    other:        "public, max-age=300"                    # everything else
```

## Security Headers

Applied to every response:

```yaml
static_client:
  security_headers:
    x_content_type_options: "nosniff"
    x_frame_options: "SAMEORIGIN"
    referrer_policy: "strict-origin-when-cross-origin"
    permissions_policy: "geolocation=(), microphone=(self), camera=()"
    content_security_policy: "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; ..."
```

The default CSP is designed for the Plexichat client. Adjust if you add
external resources.

## Rate Limiting

The static client middleware rate-limits requests per source IP using the
shared rate limiter. Two synthetic route keys are registered:

- `static_client:html` — HTML page requests (index.html, /app, /settings, ...)
- `static_client:assets` — hashed assets, /config.js, /favicon.svg, etc.

Defaults:

```yaml
rate_limiting:
  routes:
    static_client_html:
      requests: 30
      window_seconds: 60
      burst: 10
      hourly_limit: 600
    static_client_assets:
      requests: 600
      window_seconds: 60
      burst: 100
      hourly_limit: 18000
```

You can also disable rate limiting for the static client independently
via `static_client.rate_limit.enabled`.

Self-test traffic from localhost with the correct `X-Plexichat-Internal-Secret`
header is exempt.

## SPA Routes

The static client implements an SPA-style fallback: unknown paths under
configured prefixes are served the corresponding HTML file.

```yaml
static_client:
  spa_routes:
    "/app":              "app.html"
    "/settings":         "settings.html"
    "/register":         "register.html"
    "/forgot-password":  "forgot-password.html"
    "/reset-password":   "reset-password.html"
    "/oauth-callback":   "oauth-callback.html"
    "/error":            "error.html"
    "/invite":           "app.html"
```

Any other unknown path (that isn't under an API prefix) is served
`index.html`, which lets the client-side router take over.

## Config Injection

After every install, the backend writes a runtime `config.js` into the
dist directory with the detected server origin and active version:

```yaml
static_client:
  config_injection:
    enabled: true
    filename: "config.js"
    content: |
      window.PLEXICHAT_CONFIG = { serverUrl: "{origin}", hideServerField: true, defaultTheme: "ocean", version: "{version}" };
```

Disable `config_injection` if you ship your own `config.js` and don't want
it overwritten.

## Invite Redirect

```yaml
static_client:
  invite_redirect: true
```

When enabled, requests to `/invite/{code}` are 302-redirected to
`/app.html?invite={code}` so invite links land in the SPA with the code
already filled in.

## Limits

```yaml
static_client:
  max_zip_size_bytes: 104857600   # 100 MiB
```

The download is rejected if the remote `Content-Length` header exceeds
this value, and the in-memory buffer is capped to the same value.

## Logging

```yaml
static_client:
  log_downloads: false
```

Set to `true` to log every successful install with the version and target
path.

## Example: Full Configuration

```yaml
static_client:
  enabled: true
  serve: true
  install_dir: "~/.plexichat/client"
  version_pin: "match_server"
  auto_update: true
  auto_update_min_age_seconds: 3600
  auto_update_check_interval_seconds: 3600
  git_lab:
    project_id: 42
    api_url: "https://gitlab.plexichat.com/api/v4"
    private_token_env: "PLEXICHAT_GITLAB_TOKEN"
    verify_tls: true
    request_timeout_seconds: 30
  cache_control:
    hashed_assets: "public, max-age=31536000, immutable"
    html:         "no-store, max-age=0"
    other:        "public, max-age=300"
  security_headers:
    x_content_type_options: "nosniff"
    x_frame_options: "SAMEORIGIN"
    referrer_policy: "strict-origin-when-cross-origin"
    permissions_policy: "geolocation=(), microphone=(self), camera=()"
    content_security_policy: "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data: blob:; media-src 'self' blob:; worker-src blob:; connect-src 'self' wss:; manifest-src 'self'; frame-ancestors 'none';"
  rate_limit:
    enabled: true
    html:   { requests: 30,  window_seconds: 60, burst: 10 }
    assets: { requests: 600, window_seconds: 60, burst: 100 }
  max_zip_size_bytes: 104857600
  spa_routes:
    "/app":             "app.html"
    "/settings":        "settings.html"
    "/register":        "register.html"
    "/forgot-password": "forgot-password.html"
    "/reset-password":  "reset-password.html"
    "/oauth-callback":  "oauth-callback.html"
    "/error":           "error.html"
    "/invite":          "app.html"
  log_downloads: true
  config_injection:
    enabled: true
    filename: "config.js"
    content: |
      window.PLEXICHAT_CONFIG = { serverUrl: "{origin}", hideServerField: true, defaultTheme: "ocean", version: "{version}" };
  invite_redirect: true
```
