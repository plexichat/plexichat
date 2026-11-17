"""Regression tests for private focus-state leakage through visible presence.

``Presence.current_channel_id`` / ``current_server_id`` are transient
notification-suppression markers. They are not part of the public presence
contract and must never be handed to another viewer through the
``get_visible_*`` boundary, even though the REST layer also gates on
friend/shared-server membership.
"""

import pytest

from src.core.presence.models import Presence, UserStatus


@pytest.mark.presence
def test_focus_fields_not_exposed_to_other_viewer(
    presence_manager, two_users, monkeypatch
):
    viewer, target = two_users
    real = Presence(
        user_id=target.id,
        status=UserStatus.ONLINE,
        current_channel_id=42,
        current_server_id=7,
    )
    monkeypatch.setattr(presence_manager, "get_presence", lambda *a, **k: real)

    visible = presence_manager.get_visible_presence(viewer.id, target.id)
    assert visible.status is UserStatus.ONLINE
    assert visible.current_channel_id is None
    assert visible.current_server_id is None

    # Self-view keeps the real focus state (the notification path reads the
    # raw get_presence, but the visible self-projection must stay intact too).
    own = presence_manager.get_visible_presence(target.id, target.id)
    assert own.current_channel_id == 42
    assert own.current_server_id == 7


@pytest.mark.presence
def test_focus_fields_not_exposed_in_bulk(presence_manager, two_users, monkeypatch):
    viewer, target = two_users
    real = Presence(
        user_id=target.id,
        status=UserStatus.ONLINE,
        current_channel_id=99,
        current_server_id=123,
    )
    monkeypatch.setattr(presence_manager, "get_presences", lambda ids: [real])

    result = presence_manager.get_visible_presences_bulk(viewer.id, [target.id])
    assert result[target.id].current_channel_id is None
    assert result[target.id].current_server_id is None

    own = presence_manager.get_visible_presences_bulk(target.id, [target.id])
    assert own[target.id].current_channel_id == 99
    assert own[target.id].current_server_id == 123


@pytest.mark.presence
def test_invisible_and_blocked_still_render_offline(
    presence_manager, two_users, monkeypatch
):
    """The pre-existing scrub rules must survive the new focus scrub."""
    viewer, target = two_users
    real = Presence(
        user_id=target.id,
        status=UserStatus.INVISIBLE,
        current_channel_id=5,
        current_server_id=6,
    )
    monkeypatch.setattr(presence_manager, "get_presence", lambda *a, **k: real)

    visible = presence_manager.get_visible_presence(viewer.id, target.id)
    assert visible.status is UserStatus.OFFLINE
    assert visible.current_channel_id is None
    assert visible.current_server_id is None
