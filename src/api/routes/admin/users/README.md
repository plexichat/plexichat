# Admin Users Router Sub-package

This package refactors the monolithic `users.py` into a class-based,
mixin-oriented architecture. Each file contains a focused mixin class,
and the `composer.py` combines them via multiple inheritance.

## Architecture

```
admin/users/
├── __init__.py          # Module-level APIRouter + route registration
├── base.py              # AdminUsersRouterBase - shared helpers (_parse_user_id, _get_db, _get_auth)
├── search.py            # SearchMixin - GET /users/search, GET /users/scheduled-deletions
├── management.py        # ManagementMixin - GET /users/{user_id}, tier, badges, notes
├── lifecycle.py         # LifecycleMixin - force username change, cancel/delay deletion, purge
├── admin_users.py       # AdminUsersMixin - admin user CRUD + toggle status (and local schemas)
├── composer.py          # AdminUsersRouter - combines all mixins, register_routes()
└── README.md            # This file
```

## Inheritance Order

`AdminUsersRouter` uses C3 linearisation (MRO):

```
AdminUsersRouter
  → SearchMixin
  → ManagementMixin
  → LifecycleMixin
  → AdminUsersMixin
  → AdminUsersRouterBase
```

## Key Design Decisions

1. **Function → Method**: All module-level functions became instance methods
   to enable shared helper access through `self`.

2. **Decorator → register_routes()**: The `@router.get(...)` decorators are
   replaced by explicit `router.get(path, response_model=...)(method)` calls in
   `register_routes()`. All OpenAPI metadata is preserved.

3. **Module-level router**: The `__init__.py` creates the `router` instance,
   instantiates `AdminUsersRouter`, and calls `register_routes()`. The
   `router` object is the sole public export, matching the original API.

4. **Base class helpers**: `_parse_user_id`, `_get_db`, and `_get_auth` are
   extracted from repetitive try/except patterns into `AdminUsersRouterBase`,
   reducing boilerplate across mixins.

5. **Relative imports**: Mixins import shared utilities from the parent
   `admin/` package via `from ..utils import ...`.

## Adding a New Route

1. Add the route handler method to the appropriate mixin (or create a new
   mixin file if it belongs to a new category).
2. In `composer.py`, add `router.<method>("/path", ...)(self.handler)` to
   `register_routes()`.
3. If the mixin is new, add it to the `AdminUsersRouter` class definition's
   MRO list.

## Migration Note

This refactoring preserves the exact same route paths, handler signatures,
response schemas, and error handling. No functional changes were introduced.
The base class helpers (`_parse_user_id`, `_get_db`, `_get_auth`) reduce
boilerplate but behave identically to the original inline code.
