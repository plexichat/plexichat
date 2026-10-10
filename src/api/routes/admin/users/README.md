# Admin users sub-package

This package implements user administration endpoints for the admin API. It refactors a monolithic route module into a class-based, mixin-oriented architecture so each file owns a focused set of handlers.

## Overview

Each mixin provides handler methods for a category of admin user operations. The `AdminUsersRouter` class in `composer.py` combines the mixins through multiple inheritance and registers all handlers on a single FastAPI `APIRouter`. The package router in `__init__.py` is the only public export and is included by the parent `admin` router.

## Directory structure

```
admin/users/
├── __init__.py      # Creates the APIRouter, instantiates AdminUsersRouter, calls register_routes()
├── base.py          # AdminUsersRouterBase — shared helpers (_parse_user_id, _get_db, _get_auth)
├── search.py        # SearchMixin — search and scheduled-deletion listing
├── management.py    # ManagementMixin — user details, tier, badges, and notes
├── lifecycle.py     # LifecycleMixin — force username change, cancel or delay deletion, and purge
├── admin_users.py   # AdminUsersMixin — admin user CRUD and status toggle (with local schemas)
├── composer.py      # AdminUsersRouter — combines mixins and defines register_routes()
└── README.md        # This file
```

## Inheritance order

`AdminUsersRouter` uses C3 linearization. The method resolution order is:

```
AdminUsersRouter
  → SearchMixin
  → ManagementMixin
  → LifecycleMixin
  → AdminUsersMixin
  → AdminUsersRouterBase
```

Shared helpers in `AdminUsersRouterBase` are available to every mixin through `self`.

## Components

### `base.py` — shared helpers

| Helper | Description |
|--------|-------------|
| `_parse_user_id(user_id)` | Parses a string user ID as `int` or raises `400 Invalid user ID` |
| `_get_db()` | Returns the database from `src.api.get_db()` or raises `500` when unavailable |
| `_get_auth()` | Returns the auth module from `src.api.get_auth()` or raises `500` when unavailable |

### `search.py` — `SearchMixin`

| Handler | Method and path | Description |
|---------|-----------------|-------------|
| `admin_user_search` | `GET /users/search` | Searches and filters users with query parameters |
| `admin_list_scheduled_deletions` | `GET /users/scheduled-deletions` | Lists accounts pending scheduled deletion |

### `management.py` — `ManagementMixin`

| Handler | Method and path | Description |
|---------|-----------------|-------------|
| `get_user_details` | `GET /users/{user_id}` | Returns detailed profile for a single user |
| `update_user_tier` | `PUT /users/{user_id}/tier` | Updates the user's tier (premium level) |
| `add_user_badge` | `POST /users/{user_id}/badges/{badge}` | Assigns a badge to a user |
| `remove_user_badge` | `DELETE /users/{user_id}/badges/{badge}` | Removes a badge from a user |
| `get_user_notes` | `GET /users/{user_id}/notes` | Returns admin notes for a user |
| `update_user_notes` | `POST /users/{user_id}/notes` | Creates or updates admin notes for a user |

### `lifecycle.py` — `LifecycleMixin`

| Handler | Method and path | Description |
|---------|-----------------|-------------|
| `admin_force_username_change` | `POST /users/{user_id}/force-username-change` | Requires the user to change their username before continuing |
| `admin_cancel_account_deletion` | `POST /users/{user_id}/cancel-deletion` | Cancels a scheduled account deletion |
| `admin_delay_account_deletion` | `POST /users/{user_id}/delay-deletion` | Extends the scheduled deletion window |
| `admin_force_purge_account` | `POST /users/{user_id}/force-purge` | Immediately purges a scheduled account |

### `admin_users.py` — `AdminUsersMixin`

Manages admin accounts themselves, not regular users. Also defines local Pydantic schemas for admin user payloads.

| Handler | Method and path | Description |
|---------|-----------------|-------------|
| `list_admin_users` | `GET /admin-users` | Lists all admin accounts |
| `get_admin_user` | `GET /admin-users/{user_id}` | Returns a single admin account |
| `create_admin_user` | `POST /admin-users` | Creates a new admin account |
| `update_admin_user` | `PUT /admin-users/{user_id}` | Updates an existing admin account |
| `delete_admin_user` | `DELETE /admin-users/{user_id}` | Deletes an admin account |
| `toggle_admin_user_status` | `POST /admin-users/{user_id}/toggle-status` | Enables or disables an admin account |

### `composer.py` — `AdminUsersRouter`

Defines `AdminUsersRouter` and its `register_routes(router)` method. The method binds each handler to its path with explicit `response_model` and `responses` metadata using the pattern `router.get(path, response_model=...)(self.handler)`. This replaces the original `@router.get` decorators while preserving all OpenAPI metadata.

### `__init__.py` — package router

Creates the package `router = APIRouter()`, instantiates `AdminUsersRouter`, calls `register_routes(router)`, and exports only `router`. The parent `admin` package includes this router without an extra prefix, so paths appear as `/admin/users/...` and `/admin/admin-users/...`.

## Design decisions

*   **Functions become instance methods.** Module-level functions became methods so mixins can share helpers through `self`.
*   **Decorators become explicit registration.** The `@router.get(...)` pattern is replaced by `router.<method>(path, ...)(self.handler)` calls in `register_routes()`. This keeps routing metadata visible in one place and avoids decorator execution at import time.
*   **Base class helpers reduce duplication.** `_parse_user_id`, `_get_db`, and `_get_auth` replace repetitive `try` and `except` blocks and produce consistent error envelopes.
*   **Relative imports stay within the package.** Mixins that need parent utilities import from `..utils`.

## Add a new route

1.  Add the handler method to the appropriate mixin file (or create a new mixin module if the route belongs to a new category).

    ```python
    # management.py
    class ManagementMixin(AdminUsersRouterBase):
        async def suspend_user(self, user_id: str):
            user_id_int = self._parse_user_id(user_id)
            db = self._get_db()
            ...
    ```

2.  Register the path in `composer.py`:

    ```python
    router.post(
        "/users/{user_id}/suspend",
        response_model=SuccessResponse,
        responses={404: {"model": ErrorResponse, "description": "User not found"}},
    )(self.suspend_user)
    ```

3.  If you created a new mixin, add it to the `AdminUsersRouter` base-class list in `composer.py` in the desired MRO position.
4.  Include any new Pydantic schemas in `src.api.schemas.admin` or alongside the mixin when they are local to admin user management.

## Migration notes

This refactoring preserves the original route paths, handler signatures, response schemas, and error handling. No functional changes were introduced. The base class helpers behave identically to the original inline code.

## Related documentation

*   `../README.md` — parent admin route table
*   `../../README.md` — full API route table
*   `../../../schemas/admin.py` — admin response schemas
