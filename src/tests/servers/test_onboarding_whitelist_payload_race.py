"""Onboarding whitelist grants must bind the write to the authorized payload.

``complete_onboarding_step`` re-validates the whitelist author's grantability
against a *snapshot* of each role's permissions, then calls
``assign_roles_from_whitelist`` which re-reads the role by id and inserts a
``srv_member_roles`` row. The INSERT is not bound to the payload the grantability
decision actually read. A privileged edit that widens a whitelisted role in that
gap (without ``administrator``/wildcard, which the write path refuses anyway)
hands the completing member permissions the author never held -- the same
check->write laundering N642/N745 closed on the other grant paths.
"""

import json
import uuid

import pytest

BENIGN = json.dumps({"messages.send": True})
# roles.manage is a plain scope: it is not ``administrator`` and contains no
# wildcard, so assign_roles_from_whitelist's literal checks do not refuse it.
WIDENED = json.dumps({"messages.send": True, "roles.manage": True})
AUTHOR_HELD = frozenset({"onboarding.manage", "messages.send"})


def _member(servers, prefix):
    tag = uuid.uuid4().hex[:8]
    return servers._auth.register(
        f"{prefix}_{tag}", f"{prefix}_{tag}@example.com", "TestPass123!"
    )


@pytest.fixture
def setup(fresh_server):
    server, owner, servers = fresh_server

    pickable = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="Pickable",
        permissions={"messages.send": True},
    )
    servers.move_role(owner.id, pickable.id, 3)

    # The author holds onboarding.manage and messages.send, but NOT roles.manage.
    # Its rank (10) outranks the pickable role (3), so the position gate passes.
    manager_role = servers.create_role(
        user_id=owner.id,
        server_id=server.id,
        name="Onboarding Manager",
        permissions={"onboarding.manage": True, "messages.send": True},
    )
    servers.move_role(owner.id, manager_role.id, 10)

    author = _member(servers, "wl_author")
    servers.add_member(server.id, author.id)
    servers.assign_role(owner.id, server.id, author.id, manager_role.id)

    step = servers.create_onboarding_step(
        user_id=author.id,
        server_id=server.id,
        step_type=servers.OnboardingStepType.SELECT_ROLES,
        title="Pick a role",
        options={"role_ids": [pickable.id]},
    )

    joiner = _member(servers, "wl_joiner")
    servers.add_member(server.id, joiner.id)
    return server, owner, author, pickable, step, joiner, servers


def _complete(servers, server, step, joiner, pickable):
    return servers.complete_onboarding_step(
        user_id=joiner.id,
        server_id=server.id,
        step_id=step.id,
        response={"selected_roles": [pickable.id]},
    )


def test_unchanged_whitelist_still_grants(setup):
    """Control: a role that is not widened is still self-assignable."""
    server, owner, author, pickable, step, joiner, servers = setup
    assert servers.has_permission(author.id, server.id, "roles.manage") is False

    _complete(servers, server, step, joiner, pickable)

    assert pickable.id in [r.id for r in servers.get_member_roles(server.id, joiner.id)]
    assert servers.has_permission(joiner.id, server.id, "messages.send") is True


def test_widened_role_between_recheck_and_write_is_rejected(setup, monkeypatch):
    """A role widened after the grantability snapshot must not be attached."""
    server, owner, author, pickable, step, joiner, servers = setup
    assert servers.has_permission(author.id, server.id, "roles.manage") is False
    assert "roles.manage" not in AUTHOR_HELD
    assert json.loads(WIDENED)["roles.manage"] is True
    assert "*" not in json.loads(WIDENED)
    assert "administrator" not in json.loads(WIDENED)

    real_fetch_all = servers._db.fetch_all
    state = {"widened": False}

    def racing_fetch_all(sql, params=()):
        rows = real_fetch_all(sql, params)
        q = " ".join(sql.split()).lower()
        # _resolve_whitelist_roles runs inside the completion-time grantability
        # re-check. Return its benign rows, THEN widen the authoritative role:
        # the check passes on the snapshot, and the later INSERT reads the
        # escalated payload.
        if not state["widened"] and "select * from srv_roles where id in (" in q:
            state["widened"] = True
            servers._db.execute(
                "UPDATE srv_roles SET permissions = ? WHERE id = ?",
                (WIDENED, pickable.id),
            )
        return rows

    monkeypatch.setattr(servers._db, "fetch_all", racing_fetch_all)

    try:
        _complete(servers, server, step, joiner, pickable)
    except servers.OnboardingError:
        pass  # fail closed: the write was bound to the authorized snapshot
    else:
        escalated = servers.has_permission(joiner.id, server.id, "roles.manage")
        pytest.fail(
            "ESCALATION: widened role was self-assigned after a benign "
            f"grantability snapshot (roles.manage={escalated})"
        )

    # Non-vacuous: the race really landed, the authoritative payload really is
    # escalated, and the member did not receive the role.
    assert state["widened"] is True
    raw = servers._db.fetch_one(
        "SELECT permissions FROM srv_roles WHERE id = ?", (pickable.id,)
    )
    stored = raw["permissions"] if isinstance(raw, dict) else raw[0]
    assert stored == WIDENED
    assert json.loads(stored)["roles.manage"] is True
    assert pickable.id not in [r.id for r in servers.get_member_roles(server.id, joiner.id)]
    assert servers.has_permission(joiner.id, server.id, "roles.manage") is False
