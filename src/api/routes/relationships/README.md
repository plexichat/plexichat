# Relationships Router Sub-package

This package refactors the monolithic `__init__.py` into a class-based,
mixin-oriented architecture. Each file contains a focused mixin class,
and the `composer.py` combines them via multiple inheritance.

## Architecture

```
relationships/
├── __init__.py          # Module-level APIRouter + route registration
├── base.py              # RelationshipsBase — event dispatch, cache invalidation
├── listing.py           # ListingMixin — GET /@me (list all relationships)
├── friend_requests.py   # FriendRequestMixin — POST "" (create), PUT /{id}/accept
├── blocking.py          # BlockingMixin — POST /block
├── deletion.py          # DeletionMixin — DELETE /{user_id}
├── composer.py          # RelationshipsRouter — combines all mixins, register_routes()
├── helpers.py           # Schema helpers (pre-existing, unchanged)
└── README.md            # This file
```

## Inheritance Order

`RelationshipsRouter` uses C3 linearisation (MRO):

```
RelationshipsRouter
  → ListingMixin
  → FriendRequestMixin
  → BlockingMixin
  → DeletionMixin
  → RelationshipsBase
```

## Key Design Decisions

1. **Function → Method**: All module-level functions became instance methods
   so they can share state through `self._dispatch_relationship_event` and
   `self._invalidate_relationship_list_cache`.

2. **Decorator → register_routes()**: The `@router.get(...)` decorators are
   replaced by explicit `router.get(path, ...)(method)` calls in
   `register_routes()`. All OpenAPI metadata (response_model, summary,
   responses) is preserved in the composer.

3. **Module-level router**: The `__init__.py` creates the `router` instance,
   instantiates `RelationshipsRouter`, and calls `register_routes()`. The
   `router` object is the sole public export, matching the original API.

4. **Cache invalidation**: The base class hardcodes `"relationships_api"`
   as the cache prefix — functionally identical to the original fallback.

## Adding a New Route

1. Add the route handler method to the appropriate mixin (or create a new
   mixin file if it belongs to a new category).
2. In `composer.py`, add `router.<method>("/path", ...)(self.handler)` to
   `register_routes()`.
3. If the mixin is new, add it to the `RelationshipsRouter` class
   definition's MRO list.

## Migration Note

This refactoring preserves the exact same route paths, handler signatures,
caching behaviour, and response schemas. No functional changes were
introduced.
