"""Authorization decisions for role edits and channel overrides.

These two paths answered the same question differently: role edits went through
``can_manage_role`` (a hierarchy helper), while channel overrides checked only
``channels.manage`` -- so any holder of that permission could deny
``view_channel`` on a role above their own rank, which is a privilege
escalation against administrators.

Both now return a ``Decision`` from ``src.core.security.decisions``. The value
is not the boolean (that existed) but the code: ``capability_missing``,
``rank_too_low`` and ``unauthenticated`` are distinct refusals an audit log can
record, and a test can assert which one fired.
"""

from typing import Any, Dict, Optional, Sequence

from src.core.security.decisions import (
    Capability,
    Decision,
    Policy,
    Principal,
    Resource,
)

from ...permission_utils import get_highest_role_position


def build_principal(
    user_id: Any,
    user_roles: Sequence[Dict[str, Any]],
    permissions: Optional[Dict[str, bool]],
    is_owner: bool,
) -> Principal:
    """Turn the rows a handler already loaded into a decision principal."""
    granted = frozenset(name for name, value in (permissions or {}).items() if value)
    return Principal(
        user_id=user_id,
        permissions=granted,
        rank=get_highest_role_position(list(user_roles)),
        is_owner=is_owner,
    )


def role_edit_decision(
    *,
    user_id: Any,
    user_roles: Sequence[Dict[str, Any]],
    target_position: int,
    permissions: Optional[Dict[str, bool]],
    is_owner: bool,
) -> Decision:
    """May this member edit a role at ``target_position``?

    The bar is one position above the target, which is the same rule the
    previous helper implemented: you may not edit a role at or above your own.
    """
    return Policy.decide(
        Capability.MANAGE_ROLES,
        build_principal(user_id, user_roles, permissions, is_owner),
        Resource(kind="role", required_rank=target_position + 1),
    )


def override_target_decision(
    *,
    user_id: Any,
    user_roles: Sequence[Dict[str, Any]],
    target_position: int,
    permissions: Optional[Dict[str, bool]],
    is_owner: bool,
) -> Decision:
    """May this member set or delete an override for a role that high?

    Same bar as editing the role itself. Without it, ``channels.manage`` was
    enough to change what a higher-ranked role can see.
    """
    return Policy.decide(
        Capability.MANAGE_CHANNELS,
        build_principal(user_id, user_roles, permissions, is_owner),
        Resource(kind="role-override", required_rank=target_position + 1),
    )
