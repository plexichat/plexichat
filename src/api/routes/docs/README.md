# Documentation routes

This directory serves rendered documentation for the Plexichat API, admin guides, and end-user help.

## Overview

The `docs` package renders Markdown sources from the repository `docs/` directory into responsive HTML, generates navigation and search indexes, and exposes the result through a FastAPI router. For API reference content it also produces OpenAPI 3.0 output.

If you mount the router from `router/__init__.py`, you get the full documentation site. The legacy modules at the package root (`config.py`, `renderer.py`, `navigation.py`, `openapi.py`, `dynamic.py`, `theme.py`) provide the configuration and rendering primitives that the router mixins use.

## Module structure

| File | Description |
|------|-------------|
| `router/` | Modular router implementation. See `router/README.md` for details. Re-exported as `router` at `src.api.routes.docs.router`. |
| `config.py` | Site configuration: title, version, description, theme, locale, logo, and favicon. Exposes `DocsConfig`, `get_docs_config`, and `is_docs_enabled`. |
| `renderer.py` | Rendering pipeline: Markdown to HTML, syntax highlighting, table of contents, and responsive layout. |
| `navigation.py` | Navigation generation: hierarchical sidebar, breadcrumbs, cross-reference links, and search index. |
| `openapi.py` | OpenAPI generation: scans registered routes and emits OpenAPI 3.0 JSON with schemas, auth flows, and error codes. |
| `dynamic.py` | Dynamic content: live `curl` examples, changelog, server version, and rate-limit or feature availability info. |
| `theme.py` | Theming: light and dark mode, color scheme, typography, and layout options such as sidebar or full-width. |

## Router endpoints

The router registers all paths with `include_in_schema=False` because they serve HTML, not JSON API operations. Key paths include:

* Portal: ``, `/`, `/getting-started`, `/features`, `/permissions`, `/security`, `/performance`.
* Configuration: `/configuration`, `/config-authentication`, `/config-database`, `/config-valkey`, `/config-media`, `/config-voice`, `/config-websocket`, `/config-search`, `/config-rate-limiting`, `/config-api`, `/config-email`, `/config-embeds`.
* Deployment: `/deployment`, `/deployment/overview`, `/deployment/requirements`, `/deployment/getting-started`, `/deployment/postgres-migration`, `/deployment/versioning`, `/migrations`, `/deployment/env-generator`.
* Admin: `/admin`, `/admin/getting-started`, `/admin/approval-workflows`, `/admin/audit-logging`, `/admin/rbac`, `/admin/server-management`.
* Reference: `/reference`, `/reference/{page}`, `/rate-limits`, `/errors`, `/data-types`.
* WebSocket and client: `/websocket`, `/websocket/{page}`, `/client-development`, `/client-development/websocket`.
* End user: `/end-user`, `/end-user/getting-started`, `/end-user/passkeys`, `/end-user/two-factor-authentication`.
* Utilities: `/cli/overview`, `/favicon.ico`.

For the complete list, see `router/composer.py:register_routes`.

## Examples

### Mount the docs router

```python
from fastapi import FastAPI
from src.api.routes.docs.router import router as docs_router

app = FastAPI()
app.include_router(docs_router, prefix="/docs")
```

### Check cache state

```python
from src.api.routes.docs.router import get_docs_stats, clear_docs_cache

stats = get_docs_stats()
# {"cache": {"docs_entries": 12, "html_entries": 12}, "config": {"enabled": True, "path": "..."}}

clear_docs_cache()
```

## Dependencies

* `DocsConfig` and helpers from `config.py`.
* Renderer and navigation from `renderer.py` and `navigation.py`.
* FastAPI `APIRouter` and `Response` for serving HTML and the SVG favicon.

## Notes

* Markdown sources live under the repository `docs/` directory, resolved by `DocsRouterBase.DOCS_ROOT` as `Path(__file__).parents[5] / "docs"`.
* HTML and raw Markdown are cached in memory with time-to-live (TTL) and max-entries limits. Use `clear_docs_cache()` to invalidate both caches.
* The favicon handler at `/favicon.ico` returns an inline SVG to avoid `405 Method Not Allowed` errors from browsers.
