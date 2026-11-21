"""Admin access-token detail must read the usage-summary row only once.

``GET /admin/security/access-tokens/{token_id}`` builds its response from
``AuthManager.get_api_access_token_usage``. That helper calls
``get_api_access_token`` (whose row converter issues a
``COUNT(DISTINCT ip_index)/SUM(denied)`` aggregate over the token's usage
events) and then issues the *same* aggregate again as its own ``summary``
query -- just with ``COUNT(*) AS total_events`` bolted on. Both SELECTs return
the same single summary row for the same token; the second round trip is pure
overhead on an admin detail poll.

The fix threads the one summary row through to the row converter so both the
nested ``token`` object and the top-level counters are rendered from it. This
test pins one summary read and an unchanged response.
"""

from __future__ import annotations

import pytest

from src.core.auth.manager import AuthManager

pytestmark = [pytest.mark.unit]

_SUMMARY_MARKER = "COUNT(DISTINCT ip_index)"


class _CountingDB:
    """Delegate to the real DB while recording read SQL."""

    def __init__(self, db):
        object.__setattr__(self, "db", db)
        object.__setattr__(self, "reads", [])

    def __getattr__(self, name):
        attr = getattr(self.db, name)

        def wrapped(*args, **kwargs):
            if name in ("fetch_all", "fetch_one"):
                object.__setattr__(
                    self,
                    "reads",
                    self.reads + [(name, " ".join(args[0].split()))],
                )
            return attr(*args, **kwargs)

        return wrapped


def _summary_reads(counting_db):
    return [
        sql
        for _, sql in counting_db.reads
        if "auth_api_access_token_events" in sql and _SUMMARY_MARKER in sql
    ]


def test_detail_reads_usage_summary_once(auth_manager, db):
    token = auth_manager.create_api_access_token(
        "Summary perf", created_by=1, scope_mode="monitor"
    )
    assert auth_manager.verify_api_access_token(
        token.token,
        ip_address="203.0.113.10",
        user_agent="probe-agent",
        path="/api/v1/probe",
        method="GET",
    )

    counting = _CountingDB(db)
    manager = AuthManager(counting)

    detail = manager.get_api_access_token_usage(token.id)

    summary = _summary_reads(counting)
    assert len(summary) == 1, summary
    assert "total_events" in summary[0]

    # Response parity: the nested token's counters and the top-level counters
    # are rendered from the same single summary row and still agree.
    assert detail["total_events"] == 1
    assert detail["token"].use_count_total == 1
    assert detail["token"].distinct_ip_count == detail["distinct_ip_count"] == 1
    assert detail["token"].denied_count_total == detail["denied_count_total"] == 0


def test_never_used_token_still_reads_one_summary(auth_manager, db):
    """An unused token keeps its single summary read (was one, stays one).

    ``_row_to_access_token`` skips its aggregate when ``use_count_total`` is
    zero, so only the endpoint's own summary SELECT runs on this path. The
    fold must not add a second read here.
    """
    token = auth_manager.create_api_access_token("Unused", created_by=1)

    counting = _CountingDB(db)
    manager = AuthManager(counting)

    detail = manager.get_api_access_token_usage(token.id)

    assert detail["total_events"] == 0
    assert len(_summary_reads(counting)) == 1


def test_folded_response_matches_legacy_two_read_path(auth_manager, db):
    """The fold is observable only in query count, never in the payload."""
    token = auth_manager.create_api_access_token("Parity", created_by=1)
    assert auth_manager.verify_api_access_token(
        token.token,
        ip_address="203.0.113.10",
        user_agent="probe-agent",
        path="/api/v1/probe",
        method="GET",
    )

    folded_db = _CountingDB(db)
    folded_manager = AuthManager(folded_db)
    folded = folded_manager.get_api_access_token_usage(token.id)

    legacy_db = _CountingDB(db)
    legacy_manager = AuthManager(legacy_db)
    original_get = legacy_manager.get_api_access_token

    # Emulate the pre-fix path: the row converter ignores a supplied summary
    # and re-reads the same aggregate row itself.
    legacy_manager.get_api_access_token = lambda tid, **kw: original_get(tid)
    legacy = legacy_manager.get_api_access_token_usage(token.id)

    assert folded == legacy
    assert len(_summary_reads(folded_db)) == 1
    assert len(_summary_reads(legacy_db)) == 2
