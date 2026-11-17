"""Regression tests for concurrency races in the session lifecycle.

Two check-then-act races used to let a caller escape a security invariant:

* session creation counted active sessions BEFORE inserting, so concurrent
  logins could all see a free slot and pile up more than ``max_per_user``;
* refresh rotation checked the replay guard and recorded the consumed token
  in two separate steps, so two concurrent refreshes of the same token both
  passed and both handed out a usable rotated credential.

Both fixes make the security decision atomic relative to the write that
enforces it.
"""

from unittest.mock import patch

import pytest

from src.core.auth._lazy import _get_auth_manager


@pytest.fixture
def mgr():
    return _get_auth_manager().get_instance()


def _register(auth_manager, username):
    return auth_manager.register(
        username=username,
        email=f"{username}@example.com",
        password="TestPass123!",
    )


def test_concurrent_logins_cannot_exceed_session_cap(auth_manager, mgr, monkeypatch):
    """A stale pre-insert count must not let concurrent logins beat the cap."""
    user = _register(auth_manager, "lifecycle_cap_race_user")
    monkeypatch.setattr(
        mgr,
        "_config",
        {
            **mgr._config,
            "sessions": {**mgr._config.get("sessions", {}), "max_per_user": 2},
        },
    )
    # Start with one active session: a racing reader observes this stale count
    # for each login below, even after the other login has inserted its row.
    mgr._create_session(user.id, None, None, None)

    db = mgr._db
    real_fetch_one = db.fetch_one
    real_execute = db.execute
    state = {"inserted": False}

    def execute_spy(sql, params=None):
        cursor = real_execute(sql, params)
        if sql.lstrip().startswith("INSERT INTO auth_sessions"):
            state["inserted"] = True
        return cursor

    def fetch_one_spy(sql, params=None):
        if "COUNT(*) AS c FROM auth_sessions" in sql and not state["inserted"]:
            # Snapshot a concurrent reader took before our own insert landed.
            return {"c": 1}
        return real_fetch_one(sql, params)

    original_create = mgr._create_session

    def create_session(*args, **kwargs):
        # Each "concurrent" login starts with its own pre-insert world view.
        state["inserted"] = False
        return original_create(*args, **kwargs)

    with (
        patch.object(db, "fetch_one", side_effect=fetch_one_spy),
        patch.object(db, "execute", side_effect=execute_spy),
        patch.object(mgr, "_create_session", side_effect=create_session),
    ):
        mgr._create_session(user.id, None, None, None)
        mgr._create_session(user.id, None, None, None)

    assert len(mgr.get_sessions(user.id)) <= 2


def test_concurrent_refresh_of_same_token_cannot_both_rotate(
    auth_manager, mgr, db, test_user_with_token
):
    """Two refreshes that both validate the current token must not both win."""
    from src.utils import encryption

    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login(
            test_user_with_token["username"], test_user_with_token["password"]
        )
    old_token = result.token
    session_id = int(old_token.split(".", 1)[0])

    class StaleGuard:
        """Models two racers both seeing a fresh guard at the contains() gate."""

        def __init__(self):
            self.keys = set()

        def contains(self, key):
            return False

        def add(self, key):
            self.keys.add(key)

        def consume(self, key):
            if key in self.keys:
                return False
            self.keys.add(key)
            return True

    # Pin the session row to its pre-rotation hash so both calls validate the
    # same (current) token, as they would when interleaved on two workers.
    real_fetch_one = mgr._db.fetch_one
    frozen = {}

    def fetch_one_spy(sql, params=None):
        if "FROM auth_sessions WHERE id = ?" in sql and params:
            if "row" not in frozen:
                frozen["row"] = dict(real_fetch_one(sql, params))
            return dict(frozen["row"])
        return real_fetch_one(sql, params)

    with (
        patch.object(mgr._db, "fetch_one", side_effect=fetch_one_spy),
        patch(
            "src.core.auth.managers.session.lifecycle.refresh_replay_guard",
            StaleGuard(),
        ),
    ):
        first = mgr.refresh_session(old_token)
        second = mgr.refresh_session(old_token)

    assert first is not None
    assert second is None
    row = db.fetch_one(
        "SELECT revoked FROM auth_sessions WHERE id = ?", (session_id,)
    )
    assert row is not None and row["revoked"] == 1
