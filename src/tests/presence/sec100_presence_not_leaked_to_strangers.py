"""Security invariants: presence details never reach an unauthorised viewer.

Deterministic, data-driven suite.  No live database, Valkey or clock is used:
the visibility mixin is exercised with in-memory stubs, and the REST gate is
exercised with monkeypatched module lookups.  The asserted rules are:

* a stranger (no friendship, no shared server) cannot read anyone's presence;
* blocked and invisible users are rendered as a blank offline record with no
  ``custom_status`` and no ``last_seen``/``updated_at`` timestamps;
* the read route checks the relationship gate *before* returning any data.
"""

import inspect

import pytest

from src.api.routes import presence as presence_routes
from src.core.presence.manager.visibility import VisibilityMixin
from src.core.presence.models import CustomStatus, Presence, UserStatus

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.presence]

VIEWER = 100
TARGET = 200


def _presence(status, *, last_seen=1234, updated=5678, with_custom=True):
    return Presence(
        user_id=TARGET,
        status=status,
        custom_status=CustomStatus(text="private note", emoji="🎯")
        if with_custom
        else None,
        activity=None,
        last_seen=last_seen,
        updated_at=updated,
    )


class _Relationships:
    def __init__(self, friends=(), blocked=()):
        self.friends = set(friends)
        self.blocked = set(blocked)

    def get_friend_ids(self, _viewer_id):
        return list(self.friends)

    def is_blocked_by_either(self, viewer_id, target_id):
        return int(viewer_id) in self.blocked or int(target_id) in self.blocked

    def get_all_blocked_ids(self, _viewer_id):
        return list(self.blocked)

    def get_blocked_user_ids(self, _viewer_id):
        return list(self.blocked)


class _Servers:
    def __init__(self, shared=()):
        self.shared = set(shared)

    def get_all_shared_member_ids(self, _viewer_id):
        return list(self.shared)


class _VisibilityProbe(VisibilityMixin):
    """Only the methods the visibility mixin delegates to are stubbed."""

    def __init__(self, presence, relationships=None, status=None):
        self._presence = presence
        self._relationships = relationships
        self._status = status

    def get_presence(self, _user_id, use_cache=True):
        return self._presence

    def get_presences(self, user_ids):
        return [self._presence for _ in user_ids]

    def get_status(self, _user_id):
        return self._status if self._status is not None else self._presence.status


# ---------------------------------------------------------------------------
# REST read gate: friends / shared servers yes, strangers no
# ---------------------------------------------------------------------------

# (label, friends, shared servers, expected)
GATE_CASES = [
    ("self", (TARGET,), (), True),
    ("friend", (TARGET,), (), True),
    ("shared server", (), (TARGET,), True),
    ("friend plus shared server", (TARGET,), (TARGET,), True),
    ("stranger", (), (), False),
    ("blocked-only is not a friend", (), (), False),
]


@pytest.mark.parametrize("label,friends,shared,expected", GATE_CASES)
def test_presence_read_gate(monkeypatch, label, friends, shared, expected):
    monkeypatch.setattr(
        presence_routes.api, "get_relationships", lambda: _Relationships(friends)
    )
    monkeypatch.setattr(presence_routes.api, "get_servers", lambda: _Servers(shared))
    assert presence_routes._can_view_presence(VIEWER, TARGET) is expected


def test_presence_read_gate_fails_closed_when_no_graph_is_available(monkeypatch):
    monkeypatch.setattr(presence_routes.api, "get_relationships", lambda: None)
    monkeypatch.setattr(presence_routes.api, "get_servers", lambda: None)
    assert presence_routes._can_view_presence(VIEWER, TARGET) is False


def test_read_route_checks_the_gate_before_exposing_presence():
    source = inspect.getsource(presence_routes.get_user_presence)
    assert "_can_view_presence" in source
    assert "get_visible_presence" in source
    assert source.index("_can_view_presence") < source.index("get_visible_presence")
    assert "status_code=404" in source


# ---------------------------------------------------------------------------
# outbound event / response scrubbing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,expected_status,expected_custom",
    [
        ("invisible", "offline", None),
        ("online", "online", "private note"),
        ("idle", "idle", "private note"),
        ("dnd", "dnd", "private note"),
        ("offline", "offline", "private note"),
    ],
)
def test_presence_event_scrubs_invisible_state(
    status, expected_status, expected_custom
):
    data = presence_routes._build_presence_event_data(
        TARGET, status, "private note", "🎯"
    )
    assert data["user_id"] == str(TARGET)
    assert data["status"] == expected_status
    assert data["custom_status"] == expected_custom
    assert data["custom_emoji"] == (None if status == "invisible" else "🎯")


def test_presence_response_hides_never_online_timestamp():
    never = _presence(UserStatus.OFFLINE, last_seen=0, updated=0)
    response = presence_routes._presence_to_response(never, TARGET)
    assert response.last_seen is None
    assert response.custom_status == "private note"


def test_presence_response_keeps_real_timestamp_only():
    seen = _presence(UserStatus.ONLINE, last_seen=1234, updated=5678)
    response = presence_routes._presence_to_response(seen, TARGET)
    assert response.last_seen == 1234


# ---------------------------------------------------------------------------
# core visibility mixin: blocked and invisible targets render blank offline
# ---------------------------------------------------------------------------


def _assert_blank_offline(presence, target_id=TARGET):
    assert presence.user_id == target_id
    assert presence.status is UserStatus.OFFLINE
    assert presence.custom_status is None
    assert presence.activity is None
    assert presence.last_seen == 0
    assert presence.updated_at == 0


@pytest.mark.parametrize(
    "label,status,blocked,blank",
    [
        ("blocked viewer", UserStatus.ONLINE, (VIEWER,), True),
        ("invisible target", UserStatus.INVISIBLE, (), True),
        ("online friend", UserStatus.ONLINE, (), False),
        ("self", UserStatus.ONLINE, (), False),
    ],
)
def test_visible_presence_never_leaks_private_fields(label, status, blocked, blank):
    probe = _VisibilityProbe(
        _presence(status), relationships=_Relationships(blocked=blocked)
    )
    viewer = TARGET if label == "self" else VIEWER
    visible = probe.get_visible_presence(viewer, TARGET)
    if blank:
        _assert_blank_offline(visible)
    else:
        assert visible.status is status
        assert visible.custom_status.text == "private note"
        assert visible.last_seen == 1234


def test_bulk_visibility_scrubs_blocked_and_invisible_targets():
    probe = _VisibilityProbe(
        _presence(UserStatus.ONLINE), relationships=_Relationships(blocked=(TARGET,))
    )
    result = probe.get_visible_presences_bulk(VIEWER, [TARGET])
    _assert_blank_offline(result[TARGET])

    invisible = _VisibilityProbe(
        _presence(UserStatus.INVISIBLE), relationships=_Relationships()
    )
    result = invisible.get_visible_presences_bulk(VIEWER, [TARGET])
    _assert_blank_offline(result[TARGET])


@pytest.mark.parametrize(
    "status,blocked,expected",
    [
        (UserStatus.ONLINE, (), True),
        (UserStatus.ONLINE, (VIEWER,), False),
        (UserStatus.INVISIBLE, (), False),
    ],
)
def test_can_see_presence_gate(status, blocked, expected):
    probe = _VisibilityProbe(
        _presence(status), relationships=_Relationships(blocked=blocked), status=status
    )
    assert probe.can_see_presence(VIEWER, TARGET) is expected
