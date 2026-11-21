"""Regression: the admin access-token listing must batch usage aggregates.

``GET /admin/security/access-tokens`` calls
``auth.list_api_access_tokens()``.  Each row's ``distinct_ip_count`` and
``denied_count_total`` come from an aggregate over
``auth_api_access_token_events``.  Serialising a page used to issue one such
COUNT query per token (N+1), so a bounded page of used tokens cost N extra
round trips.  The aggregates for the whole (already authorized) page are now
resolved in a single ``GROUP BY token_id`` query.
"""

from unittest.mock import patch


_PAGE = 8  # bounded page of used tokens


def _make_used_tokens(auth_manager, count):
    tokens = []
    for i in range(count):
        tok = auth_manager.create_api_access_token(f"batch-{i}", created_by=1)
        auth_manager.verify_api_access_token(
            tok.token,
            ip_address=f"203.0.113.{i + 1}",
            user_agent="pytest-agent",
            path="/api/v1/users/@me",
            method="GET",
        )
        tokens.append(tok)
    return tokens


def test_list_access_tokens_batches_usage_counts(auth_manager):
    from src.core.auth._lazy import _get_auth_manager

    manager = _get_auth_manager().get_instance()
    db = manager._db
    _make_used_tokens(auth_manager, _PAGE)

    real_fetch_one = db.fetch_one
    real_fetch_all = db.fetch_all
    usage_queries = []

    def _record(query):
        normalized = " ".join(query.split())
        if (
            "FROM auth_api_access_token_events" in normalized
            and "COUNT(DISTINCT" in normalized
        ):
            usage_queries.append(normalized)

    def _spy_one(query, params=None):
        _record(query)
        return real_fetch_one(query, params)

    def _spy_all(query, params=None):
        _record(query)
        return real_fetch_all(query, params)

    with (
        patch.object(db, "fetch_one", side_effect=_spy_one),
        patch.object(db, "fetch_all", side_effect=_spy_all),
    ):
        listed = auth_manager.list_api_access_tokens(limit=_PAGE)

    assert len(listed) == _PAGE
    assert all(item.use_count_total >= 1 for item in listed)
    # Before the fix: one COUNT query per used token (_PAGE). After: one query
    # for the entire page.
    assert len(usage_queries) == 1, usage_queries
    assert "GROUP BY token_id" in usage_queries[0], usage_queries


def test_list_access_tokens_preserves_usage_values(auth_manager):
    """Batching must not change the aggregated values each row reports."""
    single = auth_manager.create_api_access_token("single-ip", created_by=1)
    auth_manager.verify_api_access_token(
        single.token, ip_address="203.0.113.10", user_agent="pytest-agent"
    )

    multi = auth_manager.create_api_access_token("multi-ip", created_by=1)
    auth_manager.verify_api_access_token(
        multi.token, ip_address="203.0.113.20", user_agent="pytest-agent"
    )
    auth_manager.verify_api_access_token(
        multi.token, ip_address="198.51.100.20", user_agent="pytest-agent"
    )

    listed = {item.id: item for item in auth_manager.list_api_access_tokens(limit=10)}

    single_row = listed[single.id]
    assert single_row.use_count_total == 1
    assert single_row.distinct_ip_count == 1
    assert single_row.denied_count_total == 0

    multi_row = listed[multi.id]
    assert multi_row.use_count_total == 2
    assert multi_row.distinct_ip_count == 2
    assert multi_row.denied_count_total == 0
