# Users Router Sub-package

This package refactors the monolithic `__init__.py` into a class-based,
mixin-oriented architecture. Each file contains a focused mixin class,
and the `composer.py` combines them via multiple inheritance.

## Architecture

```
users/
├── __init__.py          # Module-level APIRouter + route registration
├── base.py              # UsersRouterBase - cache invalidation helpers
├── profile.py           # ProfileMixin - GET/PATCH /@me, GET /{user_id}, DELETE /@me
├── avatar.py            # AvatarMixin - POST /@me/avatar
├── channel.py           # ChannelMixin - GET /@me/notes, GET/POST /@me/channels
├── discovery.py         # DiscoveryMixin - GET /search
├── settings.py          # SettingsMixin - GET/PATCH /@me/messaging-settings
├── composer.py          # UsersRouter - combines all mixins, register_routes()
├── helpers.py           # Schema helpers (pre-existing, unchanged)
└── README.md            # This file
```

## Inheritance Order

`UsersRouter` uses C3 linearisation (MRO):

```
UsersRouter
  → ProfileMixin
  → AvatarMixin
  → ChannelMixin
  → DiscoveryMixin
  → SettingsMixin
  → UsersRouterBase
```

## Key Design Decisions

1. **Function → Method**: All module-level functions became instance methods
   to enable future cross-mixin state sharing through `self`.

2. **Decorator → register_routes()**: The `@router.get(...)` decorators are
   replaced by explicit `router.get(path, ...)(method)` calls in
   `register_routes()`. All OpenAPI metadata (response_model, summary,
   responses) is preserved in the composer.

3. **Module-level router**: The `__init__.py` creates the `router` instance,
   instantiates `UsersRouter`, and calls `register_routes()`. The
   `router` object is the sole public export, matching the original API.

4. **Cache decorators preserved**: `@cached(...)` is kept as a method
   decorator in the mixins, applied before route registration — functionally
   identical to the original double-decorator pattern.

## Adding a New Route

1. Add the route handler method to the appropriate mixin (or create a new
   mixin file if it belongs to a new category).
2. In `composer.py`, add `router.<method>("/path", ...)(self.handler)` to
   `register_routes()`.
3. If the mixin is new, add it to the `UsersRouter` class definition's
   MRO list.

## Migration Note

This refactoring preserves the exact same route paths, handler signatures,
caching behaviour, response schemas, and error handling. No functional
changes were introduced.
