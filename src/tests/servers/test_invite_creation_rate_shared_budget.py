"""Regression: invite-creation rate budget must be shared across workers.

``_check_invite_creation_rate`` keeps its counter in the module-level
``_invite_creation_windows`` dict. Under ``--scale backend=N`` every worker had
its own copy, so a single user's creation budget was multiplied by N. The real
enforcement path (``_enforce_invite_quota``) now also spends a slot in the
shared, atomic rate-limit backend. This test proves the budget holds even when
each attempt runs with a fresh process-local state (simulated by clearing the
dict between "workers"), which is exactly what separate OS processes do.
"""

import pytest

from src.core.servers.handlers.members import invites


pytestmark = pytest.mark.unit

# Disable the active-invite cap checks in the helper so it only exercises the
# creation-rate gate under test (and needs no database).
_CFG = {
    "creation_window_seconds": 60,
    "creation_max_per_window": 2,
    "max_active_per_user": 0,
    "max_active_per_server": 0,
}


class _StubManager:
    @staticmethod
    def _get_timestamp():
        return 0


class _StubHandler:
    manager = _StubManager()
    db = None


def _enforce(user_id):
    invites.MemberInviteMixin._enforce_invite_quota(_StubHandler(), user_id, 1, _CFG)


def test_invite_creation_budget_is_shared_across_workers(monkeypatch):
    # One shared counter stands in for the hardened cross-worker backend. It is
    # deliberately NOT reset between the simulated workers below.
    counter = {"value": 0}
    calls = []

    def fake_check_rate_limit(key, limit, window):
        calls.append((key, limit, window))
        counter["value"] += 1
        allowed = counter["value"] <= limit
        return allowed, max(0, limit - counter["value"])

    monkeypatch.setattr("src.core.database.check_rate_limit", fake_check_rate_limit)

    allowed = 0
    denied = 0
    # Three workers, each starting with an empty process-local window registry,
    # each trying to spend the full per-user budget (2).
    for _worker in range(3):
        invites._reset_invite_windows()
        for _attempt in range(_CFG["creation_max_per_window"]):
            try:
                _enforce(987654321)
                allowed += 1
            except invites.InviteRateLimitedError:
                denied += 1

    # Pre-fix code touched no shared backend and allowed all 6 attempts (3x the
    # configured budget). The fix caps the global total at 2 and every attempt
    # reaches the shared counter.
    assert len(calls) == 6
    assert all(
        key == "invites:creation:987654321"
        and limit == _CFG["creation_max_per_window"]
        and window == _CFG["creation_window_seconds"]
        for key, limit, window in calls
    )
    assert allowed == _CFG["creation_max_per_window"]
    assert denied == 4


def test_invite_creation_rate_denies_when_shared_budget_exhausted(monkeypatch):
    """A deny from the shared backend propagates as a rate-limit error.

    ``check_rate_limit`` itself fails closed on a store fault by returning
    ``(False, 0)``; this guard ensures that verdict actually blocks the mint
    instead of being ignored.
    """
    monkeypatch.setattr(
        "src.core.database.check_rate_limit", lambda *_a, **_k: (False, 0)
    )

    invites._reset_invite_windows()
    with pytest.raises(invites.InviteRateLimitedError):
        _enforce(987654322)


def test_invite_creation_rate_fails_closed_on_limiter_error(monkeypatch):
    """If the shared limiter cannot run at all, the mint must be denied."""
    monkeypatch.setattr(
        "src.core.database.check_rate_limit",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("store down")),
    )

    invites._reset_invite_windows()
    with pytest.raises(invites.InviteRateLimitedError):
        _enforce(987654323)
