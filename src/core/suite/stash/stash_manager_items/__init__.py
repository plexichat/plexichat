"""Stash manager mixin package: item CRUD, versions, grants, tickets.

This package replaces the former single-file ``stash_manager_items.py``. The
public surface is unchanged: ``StashManagerItems`` is composed here from one
mixin per responsibility and re-exported under the original import path, so
``StashManager`` in ``manager.py`` needs no changes.
"""

from .creation import ItemCreationMixin
from .grants import ItemGrantMixin
from .listing import ItemListingMixin
from .maintenance import ItemMaintenanceMixin
from .mutations import ItemMutationMixin
from .tickets import ItemTicketMixin
from .versions import ItemVersionMixin


class StashManagerItems(
    ItemCreationMixin,
    ItemListingMixin,
    ItemMutationMixin,
    ItemVersionMixin,
    ItemGrantMixin,
    ItemTicketMixin,
    ItemMaintenanceMixin,
):
    """Item CRUD, versions, grants, and download tickets for PlexiStash."""


__all__ = ["StashManagerItems"]
