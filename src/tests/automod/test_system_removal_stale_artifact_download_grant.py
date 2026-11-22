"""AutoMod system kick/ban must not leave a stale conversation-participant
grant that keeps authorizing server-scoped artifact downloads.

The permission-checked kick/ban/leave paths call
``remove_participant_from_conversations`` which deletes the direct
``msg_participants`` rows seeded on join AND invalidates the messaging
``is_participant`` verdict cache. The AutoMod system-context executors take a
raw-SQL fallback (no permission-checked bot actor): they delete
``srv_members``/``srv_member_roles`` and replay only the *server* permission
cache invalidation. The seeded conversation participant rows survive.

``_authorize_scope`` -- the gate in front of the artifact export/download
route -- grants a server-scoped artifact to any conversation participant
whose conversation is bound to that server. With the stale participant row
still present, a just-kicked/banned member keeps downloading the server's
artifacts. This test pins the fail-closed behaviour.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes import artifacts_helpers as helpers
from src.core.automod.actions.ban import BanUserAction
from src.core.automod.actions.kick import KickUserAction
from src.core.automod.models import (
    ActionType,
    RuleAction,
    RuleType,
    Violation,
    ViolationSeverity,
)

pytestmark = [pytest.mark.automod, pytest.mark.security]


def _violation(server, victim):
    return Violation(
        id=1,
        server_id=server.id,
        channel_id=0,
        user_id=victim.id,
        message_id=None,
        rule_id=0,
        rule_type=RuleType.KEYWORD,
        matched_content="spam",
        actions_taken=[],
        severity=ViolationSeverity.MEDIUM,
        created_at=0,
    )


def _wire(monkeypatch, modules):
    """Bind the real wired managers into the api locator used by the helper."""
    # ``raising=False``: other suites may have reset the api locator globals
    # during teardown, so create the attributes if they are currently absent.
    monkeypatch.setattr(api, "get_servers", lambda: modules.servers, raising=False)
    monkeypatch.setattr(api, "get_messaging", lambda: modules.messaging, raising=False)
    monkeypatch.setattr(api, "get_db", lambda: modules._db, raising=False)


@pytest.mark.parametrize(
    "action_cls,action_type",
    [
        (KickUserAction, ActionType.KICK_USER),
        (BanUserAction, ActionType.BAN_USER),
    ],
)
def test_system_removal_revokes_artifact_download_grant(
    modules,
    automod_module,
    test_server_for_automod,
    user_pool,
    monkeypatch,
    action_cls,
    action_type,
):
    server, channel, owner = test_server_for_automod
    victim = user_pool.get_user()
    modules.servers.add_member(server.id, victim.id)
    _wire(monkeypatch, modules)

    conv_id = modules.servers.get_channel_conversation_id(channel.id)
    assert conv_id is not None

    # Before removal the victim is a live participant and the artifact view is
    # granted through the server-bound conversation membership.
    assert modules.messaging.is_participant(conv_id, victim.id) is True
    assert (
        helpers._authorize_scope(
            victim.id, conv_id, server.id, "artifact.view", author_id=owner.id
        )
        is None
    )

    executor = action_cls(modules._db, modules.servers, modules.messaging, None)
    action = RuleAction(action_type=action_type)
    assert (
        executor.execute(
            action, _violation(server, victim), context={"__system_context__": True}
        )
        is True
    )

    # The member row is gone. The system-context removal must also have
    # dropped the seeded direct participant row and its verdict cache; before
    # the fix both survived and is_participant still answered True here.
    assert modules.servers.get_member(server.id, victim.id) is None
    assert modules.messaging.is_participant(conv_id, victim.id) is False

    # The artifact export/download gate must now fail closed.
    with pytest.raises(HTTPException) as exc:
        helpers._authorize_scope(
            victim.id, conv_id, server.id, "artifact.view", author_id=owner.id
        )
    assert exc.value.status_code == 403


def test_system_removal_helper_drops_conversation_participants(monkeypatch):
    """Structural pin: the raw-SQL system fallback must replay the same
    conversation-participant cleanup the permission-checked kick/ban paths do,
    so a future refactor cannot silently drop it again."""
    import ast
    from pathlib import Path

    base = (
        Path(__file__).resolve().parents[2] / "core" / "automod" / "actions" / "base.py"
    )
    tree = ast.parse(base.read_text(encoding="utf-8"))
    fn = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_invalidate_member_authz"
        ),
        None,
    )
    assert fn is not None
    strings = {
        n.value
        for n in ast.walk(fn)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    assert "remove_participant_from_conversations" in strings
