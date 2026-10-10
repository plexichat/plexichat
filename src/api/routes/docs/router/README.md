# Docs router

This package implements the documentation router as a set of focused mixin classes.

## Overview

The `router` package refactors the documentation routes into a class-based, mixin architecture. Each file defines one mixin that handles a group of pages. `DocsRouter` combines the mixins through multiple inheritance and registers all routes on a FastAPI `APIRouter` through `register_routes()`.

To use the router, import `router` from `src.api.routes.docs.router`. That module creates a singleton `DocsRouter`, calls `register_routes()`, and re-exports `clear_docs_cache` and `get_docs_stats` for backward compatibility.

## File structure

```
router/
├── __init__.py          # Creates APIRouter, instantiates DocsRouter, re-exports helpers
├── base.py              # DocsRouterBase — DOCS_ROOT, cache helpers, stats
├── serving.py           # ServingMixin — _serve_page HTML rendering
├── core_pages.py        # CorePagesMixin — index, getting-started, features, permissions, security
├── config_pages.py      # ConfigPagesMixin — configuration/* and config-* pages
├── deployment_pages.py  # DeploymentPagesMixin — deployment/* and migrations
├── admin_pages.py       # AdminPagesMixin — admin/* pages
├── reference_pages.py   # ReferencePagesMixin — reference/*, rate-limits, errors
├── websocket_pages.py   # WebSocketPagesMixin — websocket/* and client-development/*
├── user_pages.py        # UserPagesMixin — end-user/* pages
├── env_generator.py     # EnvGeneratorMixin — env-generator, security-logout, access-blocked
├── cli_pages.py         # CliPagesMixin — cli/* pages
├── composer.py          # DocsRouter — combines all mixins, register_routes()
└── README.md            # This file
```

## Inheritance order

`DocsRouter` follows Python C3 method resolution order (MRO). The order matters: the serving mixin must appear before the page mixins that call `_serve_page`, and `DocsRouterBase` must be last so its state initializes correctly.

```python
class DocsRouter(
    ServingMixin,
    CorePagesMixin,
    ConfigPagesMixin,
    DeploymentPagesMixin,
    AdminPagesMixin,
    ReferencePagesMixin,
    WebSocketPagesMixin,
    UserPagesMixin,
    EnvGeneratorMixin,
    CliPagesMixin,
    DocsRouterBase,
): ...
```

Execution order:

1. `ServingMixin` — provides `_serve_page`.
2. `CorePagesMixin` through `CliPagesMixin` — page handlers that call `_serve_page`.
3. `DocsRouterBase` — shared cache and configuration at the base of the MRO.

## Key design decisions

### Instance methods with shared state

Module-level functions became instance methods so they share `self._docs_cache` and `self._html_cache` without global variables.

### Explicit registration

The `@router.get(...)` decorator pattern is replaced by explicit calls in `register_routes()`:

```python
def register_routes(self, router: APIRouter) -> None:
    router.get("/getting-started", include_in_schema=False)(self.docs_getting_started)
```

This keeps route paths in one place and avoids decorator side effects at import time.

### Module-level wrappers for compatibility

`__init__.py` creates a singleton `_docs_router` and re-exports:

```python
clear_docs_cache = _docs_router.clear_docs_cache
get_docs_stats = _docs_router.get_docs_stats
```

Existing callers that import these names continue to work.

### DOCS_ROOT path adjustment

`DocsRouterBase.DOCS_ROOT` is `Path(__file__).resolve().parents[5] / "docs"` — one level deeper than the original monolithic `router.py` because the file now lives at `router/base.py`.

### Lazy imports in ServingMixin

`_serve_page` imports its dependencies inside the method body to avoid circular imports at module load time.

## Add a new page

1. Add a handler method to the appropriate mixin file, or create a new mixin if the page belongs to a new category.

   ```python
   class MyPagesMixin(DocsRouterBase):
       async def docs_my_page(self, request: Request):
           return await self._serve_page(
               source_key="my-page",
               title="My page",
               current_path="/my-page",
               request=request,
           )
   ```

2. Register the path in `composer.py:register_routes`:

   ```python
   router.get("/my-page", include_in_schema=False)(self.docs_my_page)
   ```

3. If you created a new mixin, add it to the `DocsRouter` base-class list in `composer.py`.

## Migration note

This refactoring preserves paths, handler signatures, caching behavior, and HTML output from the original `router.py`. No functional changes were introduced.

## Dependencies

* `DocsRouterBase` from `base.py` for `DOCS_ROOT`, `_get_cached_value`, `_set_cached_value`, `_build_html_cache_key`, `clear_docs_cache`, and `get_docs_stats`.
* `DocsConfig` helpers from `../config.py`.
* FastAPI `APIRouter` and `Response`.

## Notes

* All docs routes use `include_in_schema=False` because they serve HTML pages, not JSON API operations.
* Caching uses class-level dictionaries (`_docs_cache`, `_html_cache`) keyed by source, title, path, and config representation, with TTL and max-entries eviction.
