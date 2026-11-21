"""Regression: API-token verification must not write last_used_at twice.

``verify_api_access_token`` runs on every authenticated request. Its mutable
side effects are intentionally kept outside the bounded introspection cache,
but the token-row UPDATE itself was issued twice on the common path:

* one combined UPDATE already sets ``last_used_at = now`` (along with UA/IP
  forensic columns and ``use_count_total``); and
* a second, conditional ``UPDATE ... SET last_used_at = ?`` re-wrote the exact
  same ``now`` value whenever the cached ``last_used_at`` was older than a
  minute.

The second write is pure redundancy -- it cannot change any row state. On a
freshly minted token ``last_used_at`` is 0, so the very first verification
already pays for both writes. This test pins the token-row write count to one
per verification so the duplicate cannot come back.
"""

import pytest

from src.core.auth._lazy import _get_auth_manager
from src.core.auth.managers.api_token import _invalidate_api_token_verify_cache

pytestmark = [pytest.mark.auth]

_IP = "203.0.113.10"
_UA = "regression-agent/1.0"


@pytest.fixture(autouse=True)
def _clear_verify_cache():
    _invalidate_api_token_verify_cache()
    yield
    _invalidate_api_token_verify_cache()


def _mgr():
    return _get_auth_manager().get_instance()


def test_verify_writes_token_row_once(auth_manager, monkeypatch):
    """A single verification issues exactly one auth_api_access_tokens UPDATE."""
    mgr = _mgr()
    token = mgr.create_api_access_token("write-dedupe", created_by=1)
    db = mgr._db

    real_execute = db.execute
    updates = []

    def spy_execute(query, params=None, **kwargs):
        if isinstance(query, str) and query.lstrip().upper().startswith(
            "UPDATE AUTH_API_ACCESS_TOKENS"
        ):
            updates.append(query)
        return real_execute(query, params, **kwargs)

    monkeypatch.setattr(db, "execute", spy_execute)

    # Fresh token: cached last_used_at == 0, so the old code took the redundant
    # second-write branch on this very first call.
    _invalidate_api_token_verify_cache()
    assert (
        mgr.verify_api_access_token(
            token.token,
            ip_address=_IP,
            user_agent=_UA,
            path="/api/v1/users/@me",
            method="GET",
        )
        is True
    )

    assert len(updates) == 1, (
        "verify_api_access_token must issue one token-row UPDATE per request; "
        f"got {len(updates)}: {updates}"
    )
    # The surviving write still persists last_used_at -- we removed a duplicate,
    # not the accounting itself.
    assert "last_used_at" in updates[0]


def test_repeat_verification_still_writes_once_per_call(auth_manager, monkeypatch):
    """The introspection cache may skip reads, but each call writes exactly once."""
    mgr = _mgr()
    token = mgr.create_api_access_token("write-dedupe-repeat", created_by=1)
    db = mgr._db

    real_execute = db.execute
    updates = []

    def spy_execute(query, params=None, **kwargs):
        if isinstance(query, str) and query.lstrip().upper().startswith(
            "UPDATE AUTH_API_ACCESS_TOKENS"
        ):
            updates.append(query)
        return real_execute(query, params, **kwargs)

    monkeypatch.setattr(db, "execute", spy_execute)

    _invalidate_api_token_verify_cache()
    n = 10
    for _ in range(n):
        assert (
            mgr.verify_api_access_token(
                token.token,
                ip_address=_IP,
                user_agent=_UA,
                path="/api/v1/messages",
                method="POST",
            )
            is True
        )

    assert len(updates) == n, (
        f"expected one token-row UPDATE per verification ({n}), got {len(updates)}"
    )
