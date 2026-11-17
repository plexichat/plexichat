"""Regression tests for paged relationship listing hydration.

The listing route used to bulk-fetch user profiles and presence for the whole
relationship graph before slicing to the requested page. Hydration must be
limited to the page that is actually returned, while preserving order and
per-status fields.
"""

from typing import Any, cast

from types import SimpleNamespace

import src.api as api
from src.api.routes.relationships.listing import ListingMixin


def _friend(user_id, friend_id, created_at=1):
    return SimpleNamespace(user_id=user_id, friend_id=friend_id, created_at=created_at)


def _incoming(sender_id, message=None, created_at=2):
    return SimpleNamespace(sender_id=sender_id, message=message, created_at=created_at)


def _outgoing(recipient_id, created_at=3):
    return SimpleNamespace(recipient_id=recipient_id, created_at=created_at)


def _blocked(blocked_id, created_at=4):
    return SimpleNamespace(blocked_id=blocked_id, created_at=created_at)


def _presence(status):
    return SimpleNamespace(status=SimpleNamespace(value=status))


def _setup(monkeypatch, *, friend_ids, incoming, outgoing, blocked):
    relationships = SimpleNamespace(
        get_all_relationships=lambda uid: {
            "friends": [_friend(1, fid) for fid in friend_ids],
            "pending_incoming": incoming,
            "pending_outgoing": outgoing,
            "blocked": blocked,
        }
    )

    auth = SimpleNamespace(
        get_user_profiles_bulk=lambda ids: {
            str(uid): {"username": f"user-{uid}", "avatar_url": f"a-{uid}"}
            for uid in ids
        }
    )

    def fake_bulk(viewer, ids):
        cast(Any, fake_bulk).calls.append((viewer, set(ids)))
        return {int(uid): _presence("online") for uid in ids}

    cast(Any, fake_bulk).calls = []
    presence = SimpleNamespace(get_visible_presences_bulk=fake_bulk)

    monkeypatch.setattr(api, "get_relationships", lambda: relationships)
    monkeypatch.setattr(api, "get_auth", lambda: auth)
    monkeypatch.setattr(api, "get_presence", lambda: presence)
    return auth, fake_bulk


def test_page_only_hydrates_visible_ids(monkeypatch):
    auth, fake_bulk = _setup(
        monkeypatch,
        friend_ids=[10, 11, 12, 13],
        incoming=[_incoming(20), _incoming(21)],
        outgoing=[_outgoing(30)],
        blocked=[_blocked(40)],
    )
    user_calls = []
    original = auth.get_user_profiles_bulk

    def recording(ids):
        user_calls.append(set(ids))
        return original(ids)

    auth.get_user_profiles_bulk = recording

    token = SimpleNamespace(user_id=1)
    # Bypass the @cached wrapper to exercise the route body directly.
    result = cast(Any, ListingMixin.get_relationships).__wrapped__(
        ListingMixin(), current_user=token, limit=2, offset=2
    )

    assert user_calls == [{12, 13}]
    assert cast(Any, fake_bulk).calls == [(1, {12, 13})]
    assert [item.user_id for item in result] == ["12", "13"]
    assert [item.status for item in result] == ["friend", "friend"]


def test_full_page_preserves_status_message_and_blocked_offline(monkeypatch):
    _setup(
        monkeypatch,
        friend_ids=[10],
        incoming=[_incoming(20, message="hello")],
        outgoing=[_outgoing(30)],
        blocked=[_blocked(40)],
    )

    token = SimpleNamespace(user_id=1)
    result = cast(Any, ListingMixin.get_relationships).__wrapped__(
        ListingMixin(), current_user=token, limit=100, offset=0
    )

    assert [item.user_id for item in result] == ["10", "20", "30", "40"]
    assert [item.status for item in result] == [
        "friend",
        "pending_incoming",
        "pending_outgoing",
        "blocked",
    ]
    assert result[1].message == "hello"
    assert result[3].presence.status == "offline"
    assert result[0].username == "user-10"
