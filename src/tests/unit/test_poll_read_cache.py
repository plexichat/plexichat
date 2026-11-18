"""Bounded, per-credential caching for the hot poll read endpoints.

``GET /polls/{id}`` and ``GET /polls/{id}/results`` used to hydrate the poll
row, its options, a voter aggregate and (for results) a per-option vote
aggregation on every request, then serialize the whole response. These tests
pin the new contract:

* a warm read is byte-for-byte identical to a cold one but performs no
  hydration query (query reduction);
* the cache key is partitioned per credential, so two users (and a bot vs. its
  owner) never share an entry -- no cross-user leak;
* the channel-access gate runs on *every* request before the cache lookup, so
  denials are never cached and a revoked membership is not served a warm body;
* any poll mutation drops every poll read entry, since counts are visible to
  other viewers.

The checks themselves are not weakened: the real route functions, key builder
and cache decorator stay on the path; only the core module is replaced by a
counting fake.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.polls import (
    _POLL_READ_DETAIL_PREFIX,
    _POLL_READ_RESULTS_PREFIX,
    delete_poll,
    get_poll,
    get_poll_results,
)
from src.core.auth.models import AccountType, TokenInfo
from src.core.database.cache.operations import invalidate_pattern
from src.core.polls.manager.reading import ReadingMixin
from src.core.polls.models import Poll, PollOption, PollResults

pytestmark = [pytest.mark.unit]

POLL_ID = 5001
MSG_ID = 5002


def _token(uid: int, token_type: str = "user", account_id: int | None = None):
    return TokenInfo(
        valid=True,
        token_type=token_type,
        account_id=account_id if account_id is not None else uid,
        user_id=uid,
        session_id=None,
        permissions={},
        rate_limit_tier="default",
        expires_at=None,
        username=f"user-{uid}",
        account_type=AccountType.USER,
    )


class _FakePolls:
    """Counting stand-in for the core polls module."""

    def __init__(self):
        self.allowed = True
        self.access_calls: list[tuple[int, int]] = []
        self.detail_calls: list[tuple[int, int]] = []
        self.results_calls: list[tuple[int, int]] = []
        self.delete_calls: list[tuple[int, int]] = []
        self.questions: dict[int, str] = {}

    def _poll(self, user_id: int) -> Poll:
        return Poll(
            id=POLL_ID,
            message_id=MSG_ID,
            question=self.questions.get(user_id, f"question for {user_id}"),
            created_by=7,
            created_at=100,
            options=[
                PollOption(id=10, poll_id=POLL_ID, text="a", position=0),
                PollOption(id=11, poll_id=POLL_ID, text="b", position=1),
            ],
            total_votes=2,
            is_ended=False,
        )

    def can_access_poll(self, poll_id: int, user_id: int) -> bool:
        self.access_calls.append((poll_id, user_id))
        return self.allowed

    def get_poll(self, poll_id: int, user_id: int) -> Poll | None:
        self.detail_calls.append((poll_id, user_id))
        return self._poll(user_id)

    def get_results(self, poll_id: int, user_id: int) -> PollResults:
        self.results_calls.append((poll_id, user_id))
        return PollResults(
            poll=self._poll(user_id),
            options=[
                PollOption(id=10, poll_id=POLL_ID, text="a", position=0, vote_count=2),
                PollOption(id=11, poll_id=POLL_ID, text="b", position=1, vote_count=1),
            ],
            total_votes=2,
            user_voted=True,
            user_votes=[10],
        )

    def delete_poll(self, user_id: int, poll_id: int) -> bool:
        self.delete_calls.append((user_id, poll_id))
        return True


@pytest.fixture(autouse=True)
def _clean_poll_cache():
    for prefix in (_POLL_READ_DETAIL_PREFIX, _POLL_READ_RESULTS_PREFIX):
        invalidate_pattern(f"{prefix}:*")
    yield
    for prefix in (_POLL_READ_DETAIL_PREFIX, _POLL_READ_RESULTS_PREFIX):
        invalidate_pattern(f"{prefix}:*")


@pytest.fixture
def fake_polls():
    fake = _FakePolls()
    with patch.object(api, "get_polls", return_value=fake):
        yield fake


@pytest.mark.asyncio
async def test_detail_hit_is_identical_and_reduces_queries(fake_polls):
    token = _token(42)
    first = await get_poll(str(POLL_ID), current_user=token)
    second = await get_poll(str(POLL_ID), current_user=token)

    assert first.model_dump() == second.model_dump()
    # One hydration across two reads: the second was served from cache.
    assert len(fake_polls.detail_calls) == 1
    # The authz gate still ran on both requests.
    assert fake_polls.access_calls == [(POLL_ID, 42), (POLL_ID, 42)]


@pytest.mark.asyncio
async def test_results_hit_is_identical_and_reduces_queries(fake_polls):
    token = _token(42)
    first = await get_poll_results(str(POLL_ID), current_user=token)
    second = await get_poll_results(str(POLL_ID), current_user=token)

    assert first.model_dump() == second.model_dump()
    assert len(fake_polls.results_calls) == 1
    assert fake_polls.access_calls == [(POLL_ID, 42), (POLL_ID, 42)]


@pytest.mark.asyncio
async def test_per_user_keys_do_not_leak(fake_polls):
    alice, bob = _token(42), _token(99)
    fake_polls.questions[42] = "alice question"
    fake_polls.questions[99] = "bob question"

    alice_answer = await get_poll(str(POLL_ID), current_user=alice)
    bob_answer = await get_poll(str(POLL_ID), current_user=bob)

    assert alice_answer.question == "alice question"
    assert bob_answer.question == "bob question"
    assert len(fake_polls.detail_calls) == 2  # distinct per-user keys

    # Repeating each read now hits its own entry; neither crosses over.
    assert (
        await get_poll(str(POLL_ID), current_user=alice)
    ).question == "alice question"
    assert (await get_poll(str(POLL_ID), current_user=bob)).question == "bob question"
    assert len(fake_polls.detail_calls) == 2


@pytest.mark.asyncio
async def test_distinct_poll_ids_do_not_collide(fake_polls):
    token = _token(42)
    await get_poll("5001", current_user=token)
    await get_poll("5002", current_user=token)

    # The poll id is part of the key; different polls are different entries.
    assert len(fake_polls.detail_calls) == 2


@pytest.mark.asyncio
async def test_bot_and_owner_tokens_do_not_share_cache(fake_polls):
    owner = _token(42)
    bot = _token(42, token_type="bot", account_id=999)

    await get_poll(str(POLL_ID), current_user=owner)
    await get_poll(str(POLL_ID), current_user=bot)

    # A bot token carries its owner's user_id; the credential-partitioned key
    # must still keep the two entries apart.
    assert len(fake_polls.detail_calls) == 2


@pytest.mark.asyncio
async def test_denials_are_never_cached_or_served_from_cache(fake_polls):
    token = _token(42)

    fake_polls.allowed = False
    with pytest.raises(HTTPException) as exc:
        await get_poll(str(POLL_ID), current_user=token)
    assert exc.value.status_code == 404
    assert fake_polls.detail_calls == []  # gate short-circuits before the loader

    fake_polls.allowed = True
    first = await get_poll(str(POLL_ID), current_user=token)
    second = await get_poll(str(POLL_ID), current_user=token)
    assert first.model_dump() == second.model_dump()
    assert len(fake_polls.detail_calls) == 1  # the denial was not cached

    # Access is revoked while a warm body still exists: the gate wins.
    fake_polls.allowed = False
    with pytest.raises(HTTPException) as exc:
        await get_poll(str(POLL_ID), current_user=token)
    assert exc.value.status_code == 404
    assert len(fake_polls.detail_calls) == 1  # warm body was never consulted


@pytest.mark.asyncio
async def test_mutation_invalidates_detail_and_results(fake_polls):
    token = _token(42)

    await get_poll(str(POLL_ID), current_user=token)
    await get_poll_results(str(POLL_ID), current_user=token)
    assert len(fake_polls.detail_calls) == 1
    assert len(fake_polls.results_calls) == 1

    # Both reads warm.
    await get_poll(str(POLL_ID), current_user=token)
    await get_poll_results(str(POLL_ID), current_user=token)
    assert len(fake_polls.detail_calls) == 1
    assert len(fake_polls.results_calls) == 1

    await delete_poll(str(POLL_ID), current_user=token)
    assert fake_polls.delete_calls == [(42, POLL_ID)]

    # A mutation dropped both namespaces: both reads are cold again.
    await get_poll(str(POLL_ID), current_user=token)
    await get_poll_results(str(POLL_ID), current_user=token)
    assert len(fake_polls.detail_calls) == 2
    assert len(fake_polls.results_calls) == 2


def test_access_gate_is_a_single_projected_query():
    """The pre-cache gate stays cheap: one message-id lookup, no hydration."""

    class _DB:
        def __init__(self):
            self.calls: list[str] = []

        def fetch_one(self, sql, params=()):
            self.calls.append(sql)
            if "SELECT message_id FROM poll_polls" in sql:
                return {"message_id": MSG_ID}
            raise AssertionError(f"unexpected query: {sql}")

    class _Manager:
        can_access_poll = ReadingMixin.can_access_poll

        def __init__(self, db):
            self._db = db

        def _require_messaging(self):
            return self

        def get_message(self, user_id, message_id):
            return {"id": message_id} if user_id == 42 else None

    db = _DB()
    manager = _Manager(db)
    assert manager.can_access_poll(POLL_ID, 42) is True
    assert manager.can_access_poll(POLL_ID, 7) is False
    assert len(db.calls) == 2
    assert all("SELECT message_id" in sql for sql in db.calls)
