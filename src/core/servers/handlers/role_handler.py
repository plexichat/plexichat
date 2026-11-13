"""Role and permission handler for server operations.

The implementation lives in :mod:`src.core.servers.handlers.roles`, split by
concern: role CRUD, channel overrides, effective permissions, and member-role
assignments. ``RoleHandler`` composes those mixins, so the import path
``src.core.servers.handlers.role_handler`` and every public method keep working
unchanged.
"""

from .roles.assignments import RoleAssignmentMixin
from .roles.crud import RoleCrudMixin
from .roles.overrides import RoleOverrideMixin
from .roles.permissions import RolePermissionMixin


class RoleHandler(
    RoleCrudMixin,
    RoleOverrideMixin,
    RolePermissionMixin,
    RoleAssignmentMixin,
):
    """Roles, channel overrides, permission resolution and assignments."""

    def __init__(self, manager):
        self.manager = manager
        self.db = manager._db
