"""Capability decisions: authorization as an object you can test and log.

The routes and core managers currently ask "does this user have permission X"
in several shapes -- a dict lookup, a wildcard match, a role-rank comparison
against the actor's own rank. Those rules are correct but each implementation
site re-derives them, and an authorization bug is the expensive kind.

``Policy.decide`` returns a ``Decision`` instead of a bare bool, so the refusal
carries a stable code and a reason that can be audited. Nothing here touches the
database or HTTP; callers build the ``Principal`` from whatever they already
loaded.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import FrozenSet, Optional


class Capability(str, Enum):
    """A named thing a principal may be allowed to do."""

    ADMINISTRATOR = "*"
    MANAGE_SERVER = "server.manage"
    MANAGE_CHANNELS = "channels.manage"
    MANAGE_ROLES = "roles.manage"
    MANAGE_EXPRESSIONS = "expressions.manage"
    BAN_MEMBERS = "members.ban"
    KICK_MEMBERS = "members.kick"
    MANAGE_MESSAGES = "messages.manage"


@dataclass(frozen=True)
class Principal:
    """Who is asking: permissions as granted, plus their rank in the hierarchy."""

    user_id: Optional[int]
    permissions: FrozenSet[str] = frozenset()
    rank: int = 0
    is_owner: bool = False

    def grants(self, capability: Capability) -> bool:
        """Wildcard-aware capability check, matching ``has_permission``."""
        if self.is_owner:
            return True
        if Capability.ADMINISTRATOR.value in self.permissions:
            return True
        if capability.value in self.permissions:
            return True
        family = capability.value.split(".", 1)[0]
        return f"{family}.*" in self.permissions


@dataclass(frozen=True)
class Resource:
    """What is being acted on, and the hierarchy bar for touching it."""

    kind: str
    id: Optional[str] = None
    owner_id: Optional[int] = None
    # Rank the principal must hold on *this* resource. A moderator editing a
    # role whose rank is above their own must be refused even though they hold
    # roles.manage, which is what the ADR calls "no privilege escalation".
    required_rank: int = 0


@dataclass(frozen=True)
class Decision:
    """The outcome, with a code stable enough to audit and assert on."""

    allowed: bool
    code: str
    reason: str

    def __bool__(self) -> bool:
        return self.allowed


class Policy:
    """Deny-by-default authorization rules."""

    #: Refusal codes. Tests assert these, so changing one is a visible change.
    UNAUTHENTICATED = "unauthenticated"
    CAPABILITY_MISSING = "capability_missing"
    RANK_TOO_LOW = "rank_too_low"
    ALLOWED = "allowed"

    @staticmethod
    def decide(
        capability: Capability,
        principal: Optional[Principal],
        resource: Resource,
    ) -> Decision:
        if principal is None or principal.user_id is None:
            return Decision(False, Policy.UNAUTHENTICATED, "No authenticated principal")

        if resource.owner_id is not None and principal.user_id == resource.owner_id:
            return Decision(True, Policy.ALLOWED, "Principal owns the resource")

        if not principal.grants(capability):
            return Decision(
                False,
                Policy.CAPABILITY_MISSING,
                f"Missing capability {capability.value}",
            )

        if resource.required_rank > principal.rank:
            return Decision(
                False,
                Policy.RANK_TOO_LOW,
                f"Rank {principal.rank} is below the required {resource.required_rank}",
            )

        return Decision(True, Policy.ALLOWED, f"Granted {capability.value}")
