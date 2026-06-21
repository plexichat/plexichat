"""Relationship manager sub-package - mixin-based split of RelationshipManager.

The :class:`RelationshipManager` was historically a single file under
``plexichat.src.core.relationships.manager``.  It was split into
multiple mixins (status, friend_requests, blocking, friends, mutual,
helpers, protocol) composed in :mod:`.composer`.  This shim re-exports
the composed class so legacy `from src.core.relationships.manager
import RelationshipManager` keeps working — the test suite, the
``rel_manager`` fixture, the route layer, and any external scripts
that imported the class by the old path.
"""

from .composer import RelationshipManager

__all__ = ["RelationshipManager"]
