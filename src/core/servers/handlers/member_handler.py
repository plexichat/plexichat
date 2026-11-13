"""
Member, ban, and invite handler for server operations.

The implementation lives in :mod:`src.core.servers.handlers.members`, split by
concern: membership lookup and mutation, bans, and invites. ``MemberHandler``
composes those mixins, so the import path
``src.core.servers.handlers.member_handler`` and every public method keep
working unchanged.
"""

from .members.bans import MemberBanMixin
from .members.crud import MemberCrudMixin
from .members.invites import MemberInviteMixin


class MemberHandler(MemberCrudMixin, MemberBanMixin, MemberInviteMixin):
    """Membership, moderation, bans and invites."""

    def __init__(self, manager):
        self.manager = manager
        self.db = manager._db
