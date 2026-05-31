# Documentation Router Sub-package

This package refactors the monolithic `router.py` into a class-based,
mixin-oriented architecture. Each file contains a focused mixin class,
and the `composer.py` combines them via multiple inheritance.

## Architecture

```
router/
├── __init__.py          # Module-level APIRouter + thin function wrappers
├── base.py              # DocsRouterBase — cache helpers, shared state
├── serving.py           # ServingMixin — _serve_page HTML rendering
├── core_pages.py        # CorePagesMixin — index, getting-started, features…
├── config_pages.py      # ConfigPagesMixin — config-*, deployment/configuration/*
├── deployment_pages.py  # DeploymentPagesMixin — deployment/*, migrations
├── admin_pages.py       # AdminPagesMixin — admin/*
├── reference_pages.py   # ReferencePagesMixin — reference/*, rate-limits, errors
├── websocket_pages.py   # WebSocketPagesMixin — websocket/*, client-development/*
├── user_pages.py        # UserPagesMixin — end-user/*
├── env_generator.py     # EnvGeneratorMixin — env-generator, security-logout, access-blocked
├── composer.py          # DocsRouter — combines all mixins, register_routes()
└── README.md            # This file
```

## Inheritance Order

`DocsRouter` uses C3 linearisation (MRO):

```
DocsRouter
  → ServingMixin
  → CorePagesMixin
  → ConfigPagesMixin
  → DeploymentPagesMixin
  → AdminPagesMixin
  → ReferencePagesMixin
  → WebSocketPagesMixin
  → UserPagesMixin
  → EnvGeneratorMixin
  → DocsRouterBase
```

The stack order is important: concrete mixins come first, the serving
mixin (which provides `_serve_page`) comes before the page mixins (which
call it), and `DocsRouterBase` sits at the bottom so its `__init__` runs
last.

## Key Design Decisions

1. **Function → Method**: All module-level functions became instance methods
   so they can share state through `self._docs_cache` / `self._html_cache`.

2. **Decorator → register_routes()**: The `@router.get(...)` decorators are
   replaced by explicit `router.get(path)(method)` calls in
   `register_routes()`.

3. **Module-level wrappers**: `clear_docs_cache()` and `get_docs_stats()`
   are module-level functions in `__init__.py` that delegate to a singleton
   `DocsRouter` instance. This preserves backward compatibility for
   consumers that import them directly.

4. **DOCS_ROOT adjustment**: The path computation uses `.parents[5]` (one
   more level than the original `router.py`) because the file is now nested
   one directory deeper (`router/base.py` vs `router.py`).

5. **ServingMixin imports**: The `_serve_page` method re-imports its
   dependencies inside the method body to avoid circular import issues
   at module load time. (In practice these are safe, but keeping the
   pattern explicit avoids surprises during refactoring.)

## Adding a New Page

1. Add the route handler method to the appropriate mixin (or create a new
   mixin file if it belongs to a new category).
2. In `composer.py`, add `router.get("/path")(self.handler_method)` to
   `register_routes()`.
3. If the mixin is new, add it to the `DocsRouter` class definition's
   MRO list.

## Migration Note

This refactoring preserves the exact same route paths, handler signatures,
caching behaviour, and HTML output. No functional changes were introduced.
