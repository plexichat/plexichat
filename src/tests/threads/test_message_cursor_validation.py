"""Regression: thread-message pagination cursors must be validated snowflakes.

``get_thread_messages`` used to parse ``before_id``/``after_id`` (and the thread
id) with a bare ``int()``. That accepted negatives, zero, identifiers beyond
the 64-bit snowflake range and non-ASCII/underscore digits, and a malformed
cursor escaped as a 500 instead of the documented 400. All three values reach
SQL lookups, so they must pass the shared snowflake guard at the boundary.
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.api.routes.threads as threads_route


def _user(user_id=123):
    return SimpleNamespace(user_id=user_id)


def _run(coro):
    return asyncio.run(coro)


class _RecordingThreads:
    def __init__(self):
        self.calls = []

    def get_messages(self, user_id, thread_id, limit=50, before_id=None, after_id=None):
        self.calls.append((user_id, thread_id, limit, before_id, after_id))
        return []


def _patch(monkeypatch, stub):
    monkeypatch.setattr(threads_route, "_get_threads", lambda: stub)


@pytest.mark.parametrize(
    "override",
    [
        {"thread_id": "-1"},
        {"thread_id": "0"},
        {"thread_id": "99000000000000000000"},
        {"thread_id": "1_0"},
        {"thread_id": "abc"},
        {"before_id": "-1"},
        {"before_id": "0"},
        {"before_id": "99000000000000000000"},
        {"before_id": "not-a-number"},
        {"before_id": "1_0"},
        {"after_id": "-5"},
        {"after_id": "0"},
        {"after_id": "٤٢"},  # Unicode digit homoglyph, accepted by int()
    ],
)
def test_invalid_thread_or_cursor_is_400_and_never_reaches_manager(
    monkeypatch, override
):
    stub = _RecordingThreads()
    _patch(monkeypatch, stub)
    params = {"thread_id": "5"}
    params.update(override)

    with pytest.raises(HTTPException) as exc:
        _run(threads_route.get_thread_messages(current_user=_user(), **params))

    assert exc.value.status_code == 400
    assert stub.calls == []


def test_valid_cursors_are_forwarded_as_ints(monkeypatch):
    stub = _RecordingThreads()
    _patch(monkeypatch, stub)

    result = _run(
        threads_route.get_thread_messages(
            "5", before_id="100", after_id="200", current_user=_user()
        )
    )

    assert result == []
    # Direct calls see FastAPI's Query default object for ``limit``; only the
    # validated ids/cursors are relevant here.
    assert len(stub.calls) == 1
    user_id, thread_id, _limit, before, after = stub.calls[0]
    assert (user_id, thread_id, before, after) == (123, 5, 100, 200)
