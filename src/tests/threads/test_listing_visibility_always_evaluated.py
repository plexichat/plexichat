"""The channel-thread visibility check must run even for an empty listing.

ANNEX AA recorded this residual: ``get_active_threads`` /
``get_archived_threads`` derived ``has_server_view`` from the threads they had
already fetched, so a channel with no (matching) threads returned ``[]``
*before* the permission was ever evaluated. Nothing leaked -- the early return
was on an empty result -- but the check was skipped rather than answered, which
is precisely the shape sweeps A-G exist to catch: an authorization decision that
does not happen.

The permission is a property of the caller and the channel, not of the rows, so
it is now resolved up front from the channel itself. These tests assert the
decision is *taken* (``channels.view`` is evaluated with the right channel) on a
channel that has no threads at all, in both the active and archived paths, and
that it still fails closed for a caller with no access.
"""

from __future__ import annotations

import pytest


def _spy_on_permission(servers, monkeypatch):
    """Record every ``has_permission`` call while preserving its result."""
    calls: list[tuple] = []
    real = servers.has_permission

    def spy(user_id, server_id, permission, channel_id=None):
        calls.append((user_id, server_id, permission, channel_id))
        return real(user_id, server_id, permission, channel_id)

    monkeypatch.setattr(servers, "has_permission", spy)
    return calls


def _empty_channel(servers, server, owner):
    return servers.create_channel(
        owner.id, server.id, "empty-authz", channel_type=servers.ChannelType.TEXT
    )


def test_empty_channel_still_evaluates_the_visibility_check(
    server_with_channel, monkeypatch
) -> None:
    owner, _member1, _member2, server, _channel, servers, threads = server_with_channel
    empty = _empty_channel(servers, server, owner)

    calls = _spy_on_permission(servers, monkeypatch)

    assert threads.get_active_threads(owner.id, empty.id) == []

    assert (owner.id, server.id, "channels.view", empty.id) in calls, (
        "the visibility check was skipped because the channel had no threads"
    )


def test_empty_channel_still_evaluates_the_check_for_archived(
    server_with_channel, monkeypatch
) -> None:
    owner, _member1, _member2, server, _channel, servers, threads = server_with_channel
    empty = _empty_channel(servers, server, owner)

    calls = _spy_on_permission(servers, monkeypatch)

    assert threads.get_archived_threads(owner.id, empty.id) == []

    assert (owner.id, server.id, "channels.view", empty.id) in calls, (
        "the archived listing skipped the visibility check on an empty channel"
    )


def test_non_member_is_evaluated_and_denied_on_an_empty_channel(
    server_with_channel, modules, monkeypatch
) -> None:
    """A caller with no access is decided against, then gets an empty list.

    The listing is deliberately silent for an unauthorised caller (it does not
    raise), so the observable contract is ``[]`` -- but the decision must have
    been made, not inferred from an empty result.
    """
    _owner, _member1, _member2, server, _channel, servers, threads = server_with_channel
    empty = _empty_channel(servers, server, _owner)

    import uuid

    unique_id = uuid.uuid4().hex[:8]
    outsider = modules.auth.register(
        username=f"outsider_{unique_id}",
        email=f"outsider_{unique_id}@example.com",
        password="TestPass123!",
    )

    calls = _spy_on_permission(servers, monkeypatch)

    assert threads.get_active_threads(outsider.id, empty.id) == []
    assert (outsider.id, server.id, "channels.view", empty.id) in calls, (
        "the non-member's refusal was inferred from an empty listing instead "
        "of being decided"
    )


def test_a_failed_check_still_hides_public_threads(
    server_with_channel, monkeypatch
) -> None:
    """The up-front decision is honoured, not just taken.

    A channel that *does* have a public thread is invisible when the check
    denies, which pins that the hoisted result feeds the filter loop.
    """
    owner, _member1, _member2, server, channel, servers, threads = server_with_channel
    threads.create_thread(user_id=owner.id, channel_id=channel.id, name="public")

    monkeypatch.setattr(
        servers,
        "has_permission",
        lambda user_id, server_id, permission, channel_id=None: False,
    )

    assert threads.get_active_threads(owner.id, channel.id) == []


@pytest.mark.parametrize("method_name", ["get_active_threads", "get_archived_threads"])
def test_check_runs_before_any_thread_rows_are_read(
    server_with_channel, monkeypatch, method_name
) -> None:
    """The permission is resolved before the listing query, not after it.

    Order matters: an early return between the query and the check is exactly
    how the check came to be skipped. Recording call order makes the regression
    impossible to reintroduce silently.
    """
    owner, _member1, _member2, server, _channel, servers, threads = server_with_channel
    empty = _empty_channel(servers, server, owner)

    order: list[str] = []
    real_has_permission = servers.has_permission

    def spy(user_id, server_id, permission, channel_id=None):
        order.append("permission")
        return real_has_permission(user_id, server_id, permission, channel_id)

    real_fetch_all = threads._db.fetch_all

    def fetch_all(sql, params=()):
        order.append("query")
        return real_fetch_all(sql, params)

    monkeypatch.setattr(servers, "has_permission", spy)
    monkeypatch.setattr(threads._db, "fetch_all", fetch_all)

    getattr(threads, method_name)(owner.id, empty.id)

    assert order[:2] == ["permission", "query"]
