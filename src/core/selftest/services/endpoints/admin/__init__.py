"""Admin endpoint tester package.

Compatibility shim: the former single-file ``admin.py`` is now a package.
``AdminMixin`` is composed from one mixin per admin area and re-exported under
the original import path, so ``tester.py`` needs no changes.
"""

from .approval import ApprovalMixin
from .deletion import DeletionMixin
from .dsar import DsarMixin
from .migrations import MigrationMixin
from .tokens import TokenMixin
from .transcript import TranscriptMixin


class AdminMixin(
    MigrationMixin,
    TokenMixin,
    DeletionMixin,
    TranscriptMixin,
    DsarMixin,
    ApprovalMixin,
):
    """Tests admin-related API endpoints."""


__all__ = ["AdminMixin"]
