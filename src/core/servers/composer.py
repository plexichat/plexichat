"""ServersManager composer - combines all mixins via MRO."""

from src.core.servers.base import ServersManagerBase
from src.core.servers.server_mixin import ServerMixin
from src.core.servers.channel_mixin import ChannelMixin
from src.core.servers.role_mixin import RoleMixin
from src.core.servers.member_mixin import MemberMixin
from src.core.servers.role_assignment_mixin import RoleAssignmentMixin
from src.core.servers.permission_mixin import PermissionMixin
from src.core.servers.invite_mixin import InviteMixin
from src.core.servers.messaging_mixin import MessagingMixin
from src.core.servers.audit_mixin import AuditMixin
from src.core.servers.event_mixin import EventMixin
from src.core.servers.template_mixin import TemplateMixin
from src.core.servers.welcome_mixin import WelcomeMixin
from src.core.servers.onboarding_mixin import OnboardingMixin


class ServersManager(
    ServerMixin,
    ChannelMixin,
    RoleMixin,
    MemberMixin,
    RoleAssignmentMixin,
    PermissionMixin,
    InviteMixin,
    MessagingMixin,
    AuditMixin,
    EventMixin,
    TemplateMixin,
    WelcomeMixin,
    OnboardingMixin,
    ServersManagerBase,
):
    """Unified servers manager combining all sub-managers via mixins.

    Inherits from:
    - ServerMixin: Server operations (create, get, update, delete, transfer)
    - ChannelMixin: Channel operations (create, get, update, delete, move)
    - RoleMixin: Role operations (create, get, update, delete, move)
    - MemberMixin: Member operations (add, get, update, remove, kick, ban)
    - RoleAssignmentMixin: Role assignment (assign, remove, get_member_roles)
    - PermissionMixin: Permission operations (has, get, require, channel override)
    - InviteMixin: Invite operations (create, get, use, delete)
    - MessagingMixin: Channel messaging (send, get messages)
    - AuditMixin: Audit log operations
    - EventMixin: Scheduled event operations
    - TemplateMixin: Template operations
    - WelcomeMixin: Welcome screen operations
    - OnboardingMixin: Onboarding operations
    - ServersManagerBase: Base class holding manager instances
    """

    pass
