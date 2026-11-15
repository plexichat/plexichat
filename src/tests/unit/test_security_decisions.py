"""Capability decisions: deny by default, with auditable refusal codes."""

from src.core.security.decisions import (
    Capability,
    Decision,
    Policy,
    Principal,
    Resource,
)


def principal(**overrides):
    defaults = {"user_id": 7, "permissions": frozenset(), "rank": 0, "is_owner": False}
    defaults.update(overrides)
    return Principal(**defaults)


RESOURCE = Resource(kind="server", id="1", owner_id=99, required_rank=0)


def test_no_permissions_is_denied():
    decision = Policy.decide(Capability.MANAGE_SERVER, principal(), RESOURCE)
    assert not decision
    assert decision.code == Policy.CAPABILITY_MISSING


def test_exact_capability_is_granted():
    decision = Policy.decide(
        Capability.MANAGE_SERVER,
        principal(permissions=frozenset({"server.manage"})),
        RESOURCE,
    )
    assert decision
    assert decision.code == Policy.ALLOWED


def test_star_wildcard_grants_everything():
    decision = Policy.decide(
        Capability.BAN_MEMBERS, principal(permissions=frozenset({"*"})), RESOURCE
    )
    assert decision


def test_family_wildcard_grants_its_family_only():
    permissive = principal(permissions=frozenset({"channels.*"}))
    assert Policy.decide(Capability.MANAGE_CHANNELS, permissive, RESOURCE)
    # A family wildcard must not leak into another family: this is the bug the
    # old substring-based checks had.
    assert not Policy.decide(Capability.MANAGE_SERVER, permissive, RESOURCE)


def test_owner_bypasses_capability_and_rank():
    owner = principal(user_id=99, rank=0, is_owner=True)
    guarded = Resource(kind="role", id="5", owner_id=99, required_rank=1000)
    decision = Policy.decide(Capability.MANAGE_ROLES, owner, guarded)
    assert decision
    assert "owns" in decision.reason


def test_server_owner_outranks_a_ceiling_with_no_role_rows():
    # The owner has no role rows, so their rank is 0; without the server-owner
    # bypass every rank check on their own roles would refuse them. This is the
    # regression that surfaced when role edits moved onto Decision.
    owner = principal(user_id=5, rank=0, is_owner=True)
    high_role = Resource(kind="role", id="9", required_rank=500)
    decision = Policy.decide(Capability.MANAGE_ROLES, owner, high_role)
    assert decision
    assert decision.code == Policy.ALLOWED


def test_a_non_owner_without_rank_is_still_refused():
    member = principal(user_id=6, rank=0, permissions=frozenset({"roles.manage"}))
    high_role = Resource(kind="role", id="9", required_rank=500)
    assert Policy.decide(Capability.MANAGE_ROLES, member, high_role).code == (
        Policy.RANK_TOO_LOW
    )


def test_rank_ceiling_blocks_privilege_escalation():
    moderator = principal(permissions=frozenset({"roles.manage"}), rank=5)
    higher_role = Resource(kind="role", id="9", required_rank=10)
    decision = Policy.decide(Capability.MANAGE_ROLES, moderator, higher_role)
    assert not decision
    assert decision.code == Policy.RANK_TOO_LOW


def test_rank_equal_to_the_bar_is_allowed():
    moderator = principal(permissions=frozenset({"roles.manage"}), rank=10)
    assert Policy.decide(
        Capability.MANAGE_ROLES,
        moderator,
        Resource(kind="role", required_rank=10),
    )


def test_unauthenticated_principal_is_refused_before_permissions():
    decision = Policy.decide(Capability.MANAGE_SERVER, None, RESOURCE)
    assert not decision
    assert decision.code == Policy.UNAUTHENTICATED

    anonymous = Principal(user_id=None, permissions=frozenset({"*"}))
    assert Policy.decide(Capability.MANAGE_SERVER, anonymous, RESOURCE).code == (
        Policy.UNAUTHENTICATED
    )


def test_decision_truthiness_and_code_are_stable():
    allowed = Decision(True, Policy.ALLOWED, "ok")
    denied = Decision(False, Policy.CAPABILITY_MISSING, "no")
    assert bool(allowed) and not bool(denied)
    # The codes are asserted by callers and appear in audit logs.
    assert Policy.UNAUTHENTICATED == "unauthenticated"
    assert Policy.CAPABILITY_MISSING == "capability_missing"
    assert Policy.RANK_TOO_LOW == "rank_too_low"
    assert Policy.ALLOWED == "allowed"
