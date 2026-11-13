"""Role, permission and assignment handlers for :class:`ServerManager`.

``handlers/role_handler.py`` used to hold all of this in one module. It is now
split by concern - CRUD, channel overrides, effective permissions and member
assignments - and composed back into ``RoleHandler`` there, so the import path
and every public method keep working unchanged.
"""

from .assignments import RoleAssignmentMixin
from .crud import RoleCrudMixin
from .overrides import RoleOverrideMixin
from .permissions import RolePermissionMixin
from .protocol import RoleHandlerProtocol

__all__ = [
    "RoleAssignmentMixin",
    "RoleCrudMixin",
    "RoleHandlerProtocol",
    "RoleOverrideMixin",
    "RolePermissionMixin",
]
