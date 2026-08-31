# Static Client

The static client module optionally serves the Plexichat web client (Vite build output) directly from the FastAPI backend. It downloads releases from the GitLab Releases API, verifies them with SHA-256, installs them to disk, and serves them through an ASGI middleware with ETag/304/Range support.

## Overview

The module handles the full lifecycle of the web client distribution:

- **Fetching**: Downloads `dist.zip` + SHA-256 from GitLab Releases, verified before unpacking.
- **Installing**: Extracts to a versioned directory under `~/.plexichat/client/`, with atomic symlink updates.
- **Serving**: ASGI middleware serves files with per-type Cache-Control, security headers, and SPA fallback.
- **Auto-updating**: Background task periodically checks for newer releases matching the server version.
- **Integrity**: HMAC-SHA-256 manifest signed via HKDF-derived key from the server KEK detects tampering.
- **Rate limiting**: Separate tiers for HTML (dynamic) and hashed assets (static).

## Architecture

```
┌──────────────────────────────────────────────────────┐
│  FastAPI App                                         │
│  ┌──────────────────────────────────────────────┐    │
│  │  StaticClientMiddleware (outermost)           │    │
│  │  ┌─────────────────────┬──────────────────┐  │    │
│  │  │  Static file serving │ SPA fallback     │  │    │
│  │  │  ETag / 304 / Range  │ /config.js       │  │    │
│  │  └─────────────────────┴──────────────────┘  │    │
│  └──────────────────────────────────────────────┘    │
│                       │                               │
│  ┌────────────────────┴──────────────────────────┐   │
│  │  StaticClientManager                          │   │
│  │  ┌────────────┬────────────┬───────────────┐  │   │
│  │  │  Fetcher   │  Manifest  │   Service     │  │   │
│  │  │  (GitLab)  │  (HMAC)   │  (auto-update)│  │   │
│  │  └────────────┴────────────┴───────────────┘  │   │
│  └───────────────────────────────────────────────┘   │
│                       │                               │
│  ┌────────────────────┴──────────────────────────┐   │
│  │  ~/.plexichat/client/<version>/                │   │
│  │  ├── index.html                                │   │
│  │  ├── assets/                                   │   │
│  │  ├── config.js (per-request generated)         │   │
│  │  ├── manifest.json + .hmac + .epoch            │   │
│  │  └── current_version                           │   │
│  └───────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────┘
```

## Key files

| File | Purpose |
|---|---|
| `__init__.py` | Public API re-exports |
| `config.py` | Typed configuration (`StaticClientConfig`, GitLab, cache, security, rate limit) |
| `manager.py` | `StaticClientManager` — install orchestrator, version selection, config.js rendering |
| `fetcher.py` | `GitLabReleaseClient` — download, verify SHA-256, extract zip, prune old versions |
| `manifest.py` | HMAC-SHA-256 manifest generation and verification, key migration detection |
| `router.py` | `StaticClientMiddleware` — ASGI middleware for file serving, SPA routing, rate limiting |
| `service.py` | Background auto-update loop (`start_static_client_service`, `stop_static_client_service`) |

## Usage

### Enable in configuration

```yaml
# config.yaml
static_client:
  enabled: true
  serve: true
  install_dir: "~/.plexichat/client"
  source: gitlab_release
  version_pin: latest
  auto_update: true
  auto_update_min_age_seconds: 3600
  git_lab:
    project_id: 12345
    api_url: "https://gitlab.plexichat.com/api/v4"
    private_token_env: PLEXICHAT_GITLAB_TOKEN
```

### Startup integration

```python
from src.static_client import (
    install_static_client_middleware,
    start_static_client_service,
    stop_static_client_service,
    run_static_client_initial_install,
)

# In FastAPI lifespan
@app.on_event("startup")
async def startup():
    run_static_client_initial_install()  # blocking initial fetch
    await start_static_client_service()  # background auto-update

@app.on_event("shutdown")
async def shutdown():
    await stop_static_client_service()

# Install middleware (outermost position)
install_static_client_middleware(app)
```

### Programmatic manager access

```python
from src.static_client import get_static_client_manager

mgr = get_static_client_manager()
if mgr:
    # Check current version
    print(mgr.current_version())

    # Force an update check
    result = mgr.ensure_active()
    if result.installed_version:
        print(f"Installed: {result.installed_version}")

    # Render runtime config.js
    config_js = mgr.render_runtime_config(origin="https://chat.example.com")
```

## Configuration

### Cache-Control headers

| Type | Default | Purpose |
|---|---|---|
| Hashed assets (`.js`, `.css`) | `public, max-age=31536000, immutable` | Content-hashed files safe to cache forever |
| HTML files | `no-store, max-age=0` | Never cache (may change between versions) |
| Other files | `public, max-age=300` | Short cache for non-hashed files |

### Security headers

Every served response includes:

| Header | Default |
|---|---|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `SAMEORIGIN` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `geolocation=(), microphone=(self), camera=()` |
| `Content-Security-Policy` | Restrictive policy (self, specific CDN allowlists) |

### Rate limiting

| Tier | Requests / window | Burst |
|---|---|---|
| HTML | 300 / 60s | 60 |
| Assets | 1200 / 60s | 120 |

HTML tier fails closed (denies 429 on limiter failure); assets tier fails open.

### Config injection

`config.js` is generated per-request so `serverUrl` reflects the actual request origin. The template can be overridden in `config.yaml` under `static_client.config_injection.content`.

Environment variable override: `PLEXICHAT_PUBLIC_SERVER_URL` sets the public server URL.

## Manifest integrity

The manifest system (`manifest.py:1-279`) signs the installed file list with HMAC-SHA-256 using a key derived from the server's KEK via HKDF. On startup:

1. Verify the HMAC signature.
2. Verify every file's SHA-256 hash against the manifest.
3. On HMAC mismatch: check for key migration (different key epoch, intact files) before re-signing.
4. On file hash mismatch or untrusted state: trigger a re-download.

## Related code

- `src/static_client/__init__.py:1` — Module docstring and public API
- `src/static_client/config.py:95` — `StaticClientConfig` dataclass
- `src/static_client/manager.py:114` — `StaticClientManager` class
- `src/static_client/fetcher.py:197` — `GitLabReleaseClient`
- `src/static_client/manifest.py:125` — `verify_manifest_detailed()`
- `src/static_client/router.py:385` — `StaticClientMiddleware`
- `src/static_client/service.py:27` — `_run_loop()` auto-update background task
