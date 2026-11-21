"""Regression: automod ban/kick owner gates must not be bypassed by id type confusion.

``BanUserAction.can_execute`` and ``KickUserAction.can_execute`` protect the
server owner from sanction with a loose ``owner_id == violation.user_id`` check.
Both ids are authoritative values (owner from ``srv_servers``, target from the
violation). A text/float encoded owner id compares unequal to the genuine int
target, skipping the guard and letting automod ban/kick the server owner.
"""

from types import SimpleNamespace

import pytest

from src.core.automod.actions.ban import BanUserAction
from src.core.automod.actions.kick import KickUserAction


def _violation(user_id, server_id=1):
    return SimpleNamespace(server_id=server_id, user_id=user_id)


def _ban_action(owner_id):
    def fetch_one(sql, params):
        if "srv_bans" in sql:
            return None
        if "srv_servers" in sql:
            return {"owner_id": owner_id}
        return None

    return BanUserAction(db=SimpleNamespace(fetch_one=fetch_one), servers_module=object())


def _kick_action(owner_id):
    def fetch_one(sql, params):
        if "srv_members" in sql:
            return {"user_id": params[1]}
        if "srv_servers" in sql:
            return {"owner_id": owner_id}
        return None

    return KickUserAction(db=SimpleNamespace(fetch_one=fetch_one), servers_module=object())


@pytest.mark.parametrize("action_factory", [_ban_action, _kick_action])
@pytest.mark.parametrize("owner_id", ["5", 5.9])
def test_encoded_owner_id_cannot_be_sanctioned(action_factory, owner_id):
    allowed, _reason = action_factory(owner_id).can_execute(None, _violation(5))
    # Pre-fix: `"5" == 5` / `5.9 == 5` is False -> owner guard skipped ->
    # (True, None) returned -> the sanction is allowed (fail open).
    assert allowed is False


@pytest.mark.parametrize("action_factory", [_ban_action, _kick_action])
def test_non_owner_target_can_still_be_sanctioned(action_factory):
    allowed, _reason = action_factory(5).can_execute(None, _violation(3))
    assert allowed is True
