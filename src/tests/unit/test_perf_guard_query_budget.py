"""Query-count budgets for the hottest read paths.

Latency ceilings are noisy; query counts are not.  Every read path below is
measured through the ``Database`` request metrics with both cache tiers
cleared, so each number is the true number of statements the path issues.
A future refactor that reintroduces per-row lookups blows a budget immediately
and deterministically, long before wall-clock time would move noticeably.

ContextVar-backed request metrics live in the calling thread, so these budgets
are measured by calling the manager methods directly (the ASGI test client
runs the app in a separate portal thread and does not propagate the counter).

Run with: pytest -m performance -n 0 src/tests/unit/test_perf_guard_query_budget.py
"""

import pytest

from src.core.database.cache.base import mem_cache_clear_pattern

pytestmark = pytest.mark.performance


def _clear(db):
    """Start from a cold cache so the measurement cannot hide extra queries."""
    mem_cache_clear_pattern("*")
    with db._query_cache_lock:
        db._query_cache.clear()
    db.reset_request_metrics()


def _assert_budget(db, label, ceiling, call):
    _clear(db)
    result = call()
    count = db.get_request_metrics()["query_count"]
    assert count <= ceiling, (
        f"{label} issued {count} queries, budget is {ceiling}"
    )
    return result


def test_message_list_query_budget(db, messaging_manager, test_dm):
    """Listing a populated channel must stay a bounded number of queries."""
    dm, user, _ = test_dm
    for i in range(10):
        messaging_manager.send_message(
            user_id=user.id, conversation_id=dm.id, content=f"budget-{i}"
        )

    messages = _assert_budget(
        db,
        "get_messages",
        12,
        lambda: messaging_manager.get_messages(
            user_id=user.id, conversation_id=dm.id, limit=50
        ),
    )
    assert len(messages) == 10


def test_token_verification_query_budget(db, auth_manager, test_user_with_token):
    """Token verification is on every authenticated request."""
    token = test_user_with_token["token"]
    info = _assert_budget(
        db, "verify_token", 4, lambda: auth_manager.verify_token(token)
    )
    assert info.valid


def test_user_and_server_read_budgets(
    db,
    auth_manager,
    server_manager,
    rel_manager,
    test_user_with_token,
    test_server,
):
    """The common identity / server / relationship reads stay cheap."""
    user_id = test_user_with_token["user"].id
    server, _ = test_server
    server_id = server.id

    _assert_budget(db, "get_user", 4, lambda: auth_manager.get_user(user_id))
    _assert_budget(db, "get_servers", 4, lambda: server_manager.get_servers(user_id))
    _assert_budget(db, "get_friends", 4, lambda: rel_manager.get_friends(user_id))
    _assert_budget(
        db,
        "get_member",
        6,
        lambda: server_manager.get_member(server_id, test_user_with_token["user"].id),
    )
