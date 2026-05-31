# Messages CRUD Sub-package

This package refactors the monolithic `messages_crud.py` into a class-based,
mixin-oriented architecture. Each file contains a focused mixin class,
and the `composer.py` combines them via multiple inheritance.

## Architecture

```
messages_crud/
├── __init__.py          # Module-level APIRouter + route registration
├── base.py              # MessagesCRUDBase — shared helpers, fallback logic
├── send.py              # SendMixin — create message, reply, poll creation
├── retrieve.py          # RetrieveMixin — get message by ID
├── edit.py              # EditMixin — edit message content
├── delete.py            # DeleteMixin — delete message, poll cleanup
├── broadcast.py         # BroadcastMixin — WebSocket event dispatch
├── composer.py          # MessagesCRUDRouter — combines all mixins, register_routes()
└── README.md            # This file
```

## Inheritance Order

MessagesCRUDRouter uses C3 linearisation (MRO):

```
MessagesCRUDRouter
  → SendMixin
  → RetrieveMixin
  → EditMixin
  → DeleteMixin
  → BroadcastMixin
  → MessagesCRUDBase
```

Concrete mixins come first, `BroadcastMixin` (providing fire-and-forget
WebSocket dispatch) sits before `MessagesCRUDBase` which provides shared
helpers.

## Key Design Decisions

1. **Function → Method**: All module-level functions became instance methods
   so they can share helpers through `self._send_message_with_fallback()`,
   `self._resolve_author_info()`, and `self._broadcast_*()` methods.

2. **Decorator → register_routes()**: The `@router.post(...)` decorators are
   replaced by explicit `router.post(path)(method)` calls in
   `register_routes()`.

3. **Broadcast extraction**: The inline WebSocket broadcast tasks that were
   duplicated across send/edit/delete routes have been extracted into
   `BroadcastMixin._broadcast_message_create()`,
   `BroadcastMixin._broadcast_message_update()`, and
   `BroadcastMixin._broadcast_message_delete()`.

4. **Author info resolution**: The shared pattern of resolving
   `author_username`, `author_avatar_url`, and `author_badges` from the
   current user token (with fallback to auth module) is extracted into
   `MessagesCRUDBase._resolve_author_info()`.

## Adding a New Route

1. Add the route handler method to the appropriate mixin (or create a new
   mixin file if it belongs to a new category).
2. In `composer.py`, add `router.<method>("/path", ...)(self.handler)` to
   `register_routes()`.
3. If the mixin is new, add it to the `MessagesCRUDRouter` class definition's
   MRO list.

## Migration Note

This refactoring preserves the exact same route paths, handler signatures,
error handling, and response schemas. No functional changes were introduced.
