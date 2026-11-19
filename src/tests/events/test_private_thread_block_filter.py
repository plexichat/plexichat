"""Private-thread channel events must honor user block relationships.

The channel-scoped emitters (typing, read receipts, reactions, polls, ...) do
not route by ``server_id``: they resolve an explicit audience and hand it to
``dispatch`` as ``user_ids``. That bypasses ``EventRouter``'s DM block filter.
``resolve_channel_event_recipients`` re-applied the filter for DM/group
participants but returned the raw member set for a known private thread, so a
thread member who blocked the actor (or whom the actor blocked) still received
the actor's ephemeral events. Regression test for that gap.
"""

from src.core.events.gateway_emit import resolve_channel_event_recipients

PRIVATE_THREAD_ID = 111
ACTOR = 1
BLOCKER = 2  # blocked the actor
BLOCKED_BY_ACTOR = 3  # blocked by the actor
CLEAN = 4
MEMBERS = [ACTOR, BLOCKER, BLOCKED_BY_ACTOR, CLEAN]


class _FakeRelationships:
    def __init__(self, blocks):
        self._blocks = blocks

    def get_all_blocked_ids(self, user_id):
        return list(self._blocks.get(user_id, []))


def _present_private_thread(monkeypatch, members):
    from src.core import threads

    monkeypatch.setattr(threads, "is_setup", lambda: True)
    monkeypatch.setattr(threads, "get_thread_type_value", lambda tid: "private")
    monkeypatch.setattr(
        threads, "get_thread_member_user_ids", lambda tid: list(members)
    )


def _patch_api(monkeypatch, relationships):
    import src.api as api

    monkeypatch.setattr(api, "get_relationships", lambda: relationships)
    # No server/messaging source: the private-thread branch is authoritative.
    monkeypatch.setattr(api, "get_servers", lambda: None)
    monkeypatch.setattr(api, "get_messaging", lambda: None)


def test_private_thread_events_exclude_blocked_members(monkeypatch):
    """A blocked pair on a private thread must not see each other's events."""
    _present_private_thread(monkeypatch, MEMBERS)
    # BLOCKER blocked ACTOR; ACTOR blocked BLOCKED_BY_ACTOR.
    _patch_api(
        monkeypatch,
        _FakeRelationships({BLOCKER: [ACTOR], ACTOR: [BLOCKED_BY_ACTOR]}),
    )

    recipients = resolve_channel_event_recipients(PRIVATE_THREAD_ID, actor_id=ACTOR)

    assert recipients == [CLEAN]


def test_private_thread_events_keep_unblocked_members(monkeypatch):
    """The filter must not silence legitimate private-thread delivery."""
    _present_private_thread(monkeypatch, MEMBERS)
    _patch_api(monkeypatch, _FakeRelationships({}))

    recipients = resolve_channel_event_recipients(PRIVATE_THREAD_ID, actor_id=ACTOR)

    assert recipients == [BLOCKER, BLOCKED_BY_ACTOR, CLEAN]


def test_private_thread_events_keep_audience_without_relationship_source(monkeypatch):
    """No relationship source keeps the audience (router parity, no silencing)."""
    _present_private_thread(monkeypatch, MEMBERS)
    _patch_api(monkeypatch, None)

    recipients = resolve_channel_event_recipients(PRIVATE_THREAD_ID, actor_id=ACTOR)

    assert recipients == [BLOCKER, BLOCKED_BY_ACTOR, CLEAN]
