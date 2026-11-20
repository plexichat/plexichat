"""Concurrency regression for the active-invite caps.

``MemberInviteMixin.create_invite`` used to do a ``SELECT COUNT(*)`` of live
invites and then a separate ``INSERT``. Two concurrent creators could both
read a count below the cap and both insert, pushing a user/server over the
limit. The cap is now enforced by a single atomic guarded INSERT; this test
forces every creator to observe a below-cap count before any write so the
race is deterministic.

The guard is a barrier in ``generate_invite_code`` (called after the advisory
cap check but before the INSERT): all workers reach it with a below-cap view,
then are released together to write.
"""

import threading

import pytest

import utils.config as config
from src.core.servers.exceptions import InviteLimitReachedError
from src.core.servers.handlers.members.invites import _reset_invite_windows


pytestmark = pytest.mark.unit


def test_concurrent_create_respects_active_invite_cap(
    server_with_channels, monkeypatch
):
    """A burst of simultaneous creates may fill a cap but never exceed it."""
    _server, owner, _admin, _member, _outsider, general, _ann, _priv, _cat, servers = (
        server_with_channels
    )

    limit = 3
    worker_count = 8

    old_cfg = config.get("invites", {}) or {}
    config.set(
        "invites",
        {
            **old_cfg,
            "max_active_per_user": limit,
            "max_active_per_server": limit,
            # Keep the in-process creation rate limiter out of the way so the
            # only gate under test is the active-invite cap.
            "creation_max_per_window": 1000,
        },
    )
    _reset_invite_windows()

    handler = servers.member_handler
    original_code = handler.generate_invite_code
    barrier = threading.Barrier(worker_count)

    def gated_code(*args, **kwargs):
        # Every worker pauses here AFTER the advisory count check and BEFORE
        # the write, so all of them see a count below the cap. The old
        # SELECT-then-INSERT path then inserted all `worker_count` invites.
        barrier.wait(timeout=20)
        return original_code(*args, **kwargs)

    monkeypatch.setattr(handler, "generate_invite_code", gated_code)

    outcomes = []

    def worker():
        try:
            servers.create_invite(user_id=owner.id, channel_id=general.id)
        except Exception as exc:  # noqa: BLE001 - collect every outcome
            outcomes.append(exc)
        else:
            outcomes.append(None)

    threads = [threading.Thread(target=worker) for _ in range(worker_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "a creator thread hung"

    successes = [o for o in outcomes if o is None]
    limit_errors = [o for o in outcomes if isinstance(o, InviteLimitReachedError)]

    # Exactly the cap's worth of creators win; every loser gets a clean cap
    # error (not a stray exception or a phantom invite).
    assert len(successes) == limit, outcomes
    assert len(limit_errors) == worker_count - limit, outcomes

    active = servers.db.fetch_one(
        "SELECT COUNT(*) AS c FROM srv_invites WHERE inviter_id = ? AND revoked = 0",
        (owner.id,),
    )
    assert active["c"] == limit
