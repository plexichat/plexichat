# Static client configuration

You can serve the Plexichat web client (`plexichat-client` Vite build) directly from the FastAPI backend. When you enable this feature, the backend downloads the matching release from GitLab, verifies the SHA-256 hash, and serves the files. This removes the need for a separate Nginx deployment for the client.

## Configuration location

Add the settings to your `config.yaml` file under the `static_client` key.

## Core settings

### Enable and disable

```yaml
static_client:
  enabled: false            # Master switch. Default is false.
  serve: true               # Serve files when enabled
  install_dir: "~/.plexichat/client"  # Directory where releases are unpacked
```

*   When `enabled` is `false`, Plexichat does not fetch releases and does not install middleware.
*   When `serve` is `false`, Plexichat can still manage installs in the background without serving files. Use this to warm the cache.

The default `install_dir` is `~/.plexichat/client` (`src/config_defaults.py:846`). The typed loader in `src/static_client/config.py:120` expands `~` and falls back to that path if the configured value is not absolute.

### Version pin

```yaml
static_client:
  version_pin: "match_server"   # Default
```

`version_pin` controls which release Plexichat installs:

*   `match_server` (default): Installs the highest build with the same stage (alpha, beta, release), major version, and minor version as the server. This is the safest option. Defined in `src/config_defaults.py:848`.
*   `latest`: Installs the latest release for the same stage and major version, regardless of minor version or build number.
*   Explicit version such as `a.1.0-84`: Pins the install to that exact release.

> **Note:** The typed config in `src/static_client/config.py:206` falls back to `latest` when the key is missing at runtime. The canonical default in `src/config_defaults.py` is `match_server`. Set `version_pin` explicitly to avoid ambiguity.

## Update behavior

```yaml
static_client:
  auto_update: false                      # Default is false
  auto_update_min_age_seconds: 3600      # Do not install releases younger than this
  auto_update_check_interval_seconds: 3600  # How often to check for a new release
```

When you set `auto_update` to `true`, a background task runs at `auto_update_check_interval_seconds` intervals and reapplies the `version_pin` logic. Plexichat installs a newer release only after it is at least `auto_update_min_age_seconds` old. This delay lets downstream caches warm and avoids races with a newly published tag.

Plexichat keeps only the active version and prunes older installs.

The runtime type `src/static_client/config.py:103` defaults `auto_update` to `true` when the key is absent. The canonical default in `src/config_defaults.py:849` is `false`. Declare the value explicitly in `config.yaml`.

## GitLab source

Plexichat pulls releases from the GitLab Releases API.

```yaml
static_client:
  git_lab:
    project_id: 2                         # GitLab project ID for plexichat-client
    api_url: "https://gitlab.plexichat.com/api/v4"
    private_token_env: "PLEXICHAT_GITLAB_TOKEN"  # Environment variable that holds the token
    verify_tls: true
    request_timeout_seconds: 30
```

1.  Create a GitLab access token with the `read_api` scope.
2.  Set the environment variable before starting the server:

    ```bash
    export PLEXICHAT_GITLAB_TOKEN="glpat-xxxxxxxxxxxxxxxxxxxx"
    ```

The loader in `src/static_client/config.py:28` reads the token with `os.environ.get(private_token_env)`.

## Cache-Control

Plexichat sends different `Cache-Control` headers by content type:

```yaml
static_client:
  cache_control:
    hashed_assets: "public, max-age=31536000, immutable"  # /assets/* with content hash
    html:         "no-store, max-age=0"                    # HTML entry points
    other:        "public, max-age=300"                    # Everything else
```

Defaults are defined in `src/config_defaults.py:859` and in `src/static_client/config.py:37`.

## Security headers

Plexichat adds these headers to every static response:

```yaml
static_client:
  security_headers:
    x_content_type_options: "nosniff"
    x_frame_options: "SAMEORIGIN"
    referrer_policy: "strict-origin-when-cross-origin"
    permissions_policy: "geolocation=(), microphone=(self), camera=()"
    content_security_policy: "default-src 'self'; ..."
```

The default CSP in `src/config_defaults.py:869` allows scripts from `self` and `https://cdn.jsdelivr.net` and fonts from `https://fonts.googleapis.com`. Update the CSP if you load external resources.

The typed config in `src/static_client/config.py:56` uses a shorter default CSP that allows `wss:` for WebSocket connections. Plexichat uses `src/config_defaults.py:869` when you install from defaults.

## Rate limiting

Plexichat rate-limits static responses per client IP.

Two layers apply:

1.  **Static client middleware** — `static_client.rate_limit` in `src/config_defaults.py:871` and `src/static_client/config.py:74`:

    ```yaml
    static_client:
      rate_limit:
        enabled: true
        html:   { requests: 300, window_seconds: 60, burst: 60 }   # HTML pages
        assets: { requests: 1200, window_seconds: 60, burst: 120 }  # Hashed assets
    ```

2.  **Global rate limiter** — `rate_limiting.routes` in `src/config_defaults.py:291`:

    ```yaml
    rate_limiting:
      routes:
        static_client_html:
          requests: 300
          window_seconds: 60.0
          burst: 100
          hourly_limit: 6000
        static_client_assets:
          requests: 5000
          window_seconds: 60.0
          burst: 1000
          hourly_limit: 180000
    ```

You can disable the static-client middleware limiter with `static_client.rate_limit.enabled: false` without affecting the global limiter.

Requests from `127.0.0.1` or `::1` that include a valid `X-Plexichat-Internal-Secret` header bypass rate limiting.

## SPA routes

Plexichat serves an SPA fallback for unknown paths. Configure the fallback in `src/config_defaults.py:877`:

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

*   If the request path starts with a configured prefix, Plexichat serves the mapped HTML file.
*   For any other unknown path that is not an API prefix, Plexichat serves `index.html` and lets the client router handle navigation.

The loader in `src/static_client/config.py:127` sorts routes by longest prefix first and normalizes prefixes to start with `/`.

## Config injection

After each install, Plexichat writes a runtime `config.js` file to the distribution directory:

```yaml
static_client:
  config_injection:
    enabled: true
    filename: "config.js"
    public_server_url: ""   # Optional fixed public URL
    content: |
      window.PLEXICHAT_CONFIG = { serverUrl: "{origin}", hideServerField: true, defaultTheme: "ocean", version: "{version}" };
```

*   When `public_server_url` is empty (the default), Plexichat derives `serverUrl` from the request `Host` or `Origin` header. The same instance works on any hostname without extra configuration.
*   Set `public_server_url` only when you must pin a specific origin, for example when a proxy rewrites `Host`.
*   In Docker, set the environment variable `PLEXICHAT_PUBLIC_SERVER_URL` (for example `https://chat.example.com`). The compose file forwards that variable to the container and `src/static_client/config.py:195` prefers it over the YAML value.
*   Placeholders `{origin}` and `{version}` in `content` are replaced at install time.
*   Set `enabled: false` if you ship your own `config.js` and do not want Plexichat to overwrite it.

## Invite redirect

```yaml
static_client:
  invite_redirect: true
```

When `invite_redirect` is `true`, a request to `/invite/{code}` returns a `302` redirect to `/app.html?invite={code}` so the client can capture the invite code.

## Limits

```yaml
static_client:
  max_zip_size_bytes: 104857600   # 100 MiB. Defined in src/config_defaults.py:876
```

Plexichat rejects the download if the remote `Content-Length` header exceeds this value and caps the in-memory buffer to the same size.

## Logging

```yaml
static_client:
  log_downloads: false
```

Set `log_downloads` to `true` to log each successful install with its version and target path.

## Full example

```yaml
static_client:
  enabled: true
  serve: true
  install_dir: "~/.plexichat/client"
  version_pin: "match_server"
  auto_update: false
  auto_update_min_age_seconds: 3600
  auto_update_check_interval_seconds: 3600
  git_lab:
    project_id: 2
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
    content_security_policy: "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: blob:; media-src 'self' blob: data:; worker-src blob:; connect-src 'self' http://localhost:8000 http://127.0.0.1:8000 wss:; manifest-src 'self'; frame-ancestors 'none';"
  rate_limit:
    enabled: true
    html:   { requests: 300, window_seconds: 60, burst: 60 }
    assets: { requests: 1200, window_seconds: 60, burst: 120 }
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
  log_downloads: false
  config_injection:
    enabled: true
    filename: "config.js"
    public_server_url: ""
    content: |
      window.PLEXICHAT_CONFIG = { serverUrl: "{origin}", hideServerField: true, defaultTheme: "ocean", version: "{version}" };
  invite_redirect: true

# Global rate limits also apply
rate_limiting:
  routes:
    static_client_html:
      requests: 300
      window_seconds: 60.0
      burst: 100
      hourly_limit: 6000
    static_client_assets:
      requests: 5000
      window_seconds: 60.0
      burst: 1000
      hourly_limit: 180000
```
