"""Regression: a failing two-way block lookup must not widen presence visibility.

``VisibilityMixin.get_visible_presences_bulk`` is the shared read gate used by
the relationship listing, the server-member list, the DM/relationship event
hydration and the guild-member chunk. It renders a target's real presence only
when the viewer is not blocked by *either* side of the relationship.

The authoritative two-way set is ``get_all_blocked_ids(viewer)``. On a failure
the previous code fell back to ``get_blocked_user_ids(viewer)`` -- the ONE-WAY
set of users the viewer blocked. That fallback is not fail-closed:

* a user who BLOCKED the viewer (but whom the viewer did not block) is absent
  from the one-way set, so their real online status/custom status/last_seen was
  rendered to the very viewer they blocked; and
* when both lookups failed, an empty ``blocked_ids`` was used and the whole gate
  silently degraded to "no blocks at all", leaking every target's real
  presence.

Withholding presence for the whole batch on an unverifiable block set is the
only safe behaviour (the event fan-out and typing filters already fail closed).
"""

import pytest

from src.core.presence.manager.visibility import VisibilityMixin
from src.core.presence.models import CustomStatus, Presence, UserStatus

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.presence]

VIEWER = 9101
BLOCKER = 9102  # BLOCKER blocked VIEWER (one-way, from VIEWER's perspective)
FRIEND = 9103


def _online(uid):
    return Presence(
        user_id=uid,
        status=UserStatus.ONLINE,
        custom_status=CustomStatus(text="private note", emoji="🎯"),
        activity=None,
        last_seen=1234,
        updated_at=5678,
    )


class _Relationships:
    """Block store whose authoritative two-way lookup can be made to fail."""

    def __init__(self, *, fail_all=False, fail_one_way=False, returned=()):
        self.fail_all = fail_all
        self.fail_one_way = fail_one_way
        self.returned = set(returned)

    def get_all_blocked_ids(self, _viewer_id):
        if self.fail_all:
            raise RuntimeError("authoritative two-way block store unavailable")
        return list(self.returned)

    def get_blocked_user_ids(self, _viewer_id):
        if self.fail_one_way:
            raise RuntimeError("one-way block store unavailable")
        # The viewer has not blocked BLOCKER; BLOCKER blocked the viewer.
        return []


class _VisibilityProbe(VisibilityMixin):
    def __init__(self, relationships=None):
        self._relationships = relationships

    def get_presences(self, user_ids):
        return [_online(uid) for uid in user_ids]


def _assert_blank_offline(presence, uid):
    assert presence.user_id == uid
    assert presence.status is UserStatus.OFFLINE
    assert presence.custom_status is None
    assert presence.activity is None
    assert presence.last_seen == 0
    assert presence.updated_at == 0


def test_two_way_lookup_failure_withholds_blocked_by_other_target():
    """A "blocked by" counterparty must stay offline if the union lookup fails."""
    probe = _VisibilityProbe(_Relationships(fail_all=True))

    result = probe.get_visible_presences_bulk(VIEWER, [BLOCKER])

    _assert_blank_offline(result[BLOCKER], BLOCKER)


def test_both_block_lookups_failing_withholds_every_target():
    """No authoritative block set means no target is proven safe to render."""
    probe = _VisibilityProbe(_Relationships(fail_all=True, fail_one_way=True))

    result = probe.get_visible_presences_bulk(VIEWER, [BLOCKER, FRIEND])

    _assert_blank_offline(result[BLOCKER], BLOCKER)
    _assert_blank_offline(result[FRIEND], FRIEND)


def test_failed_lookup_still_shows_viewer_their_own_presence():
    """Self-view is never a cross-user disclosure and must survive the failure."""
    probe = _VisibilityProbe(_Relationships(fail_all=True, fail_one_way=True))

    result = probe.get_visible_presences_bulk(VIEWER, [VIEWER])

    assert result[VIEWER].status is UserStatus.ONLINE
    assert result[VIEWER].custom_status.text == "private note"


def test_healthy_two_way_lookup_still_renders_allowed_targets(monkeypatch):
    """Non-vacuous control: with a healthy lookup real presence is returned."""
    probe = _VisibilityProbe(_Relationships(fail_all=False, returned=(BLOCKER,)))

    result = probe.get_visible_presences_bulk(VIEWER, [BLOCKER, FRIEND])

    _assert_blank_offline(result[BLOCKER], BLOCKER)
    assert result[FRIEND].status is UserStatus.ONLINE
    assert result[FRIEND].custom_status.text == "private note"
