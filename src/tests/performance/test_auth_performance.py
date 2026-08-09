"""
Authentication performance and load tests.

Functional coverage of the authentication critical paths (registration,
login, token validation, session management, concurrency). Timing is not
asserted directly: these verify correctness under repeated load so a
regression surfaces as a deterministic failure instead of a wall-clock
flake.

Run with: pytest -m performance
"""

import threading
import uuid

import pytest

pytestmark = pytest.mark.performance


def _register(auth_manager, tag):
    username = f"{tag}_{uuid.uuid4().hex[:8]}"
    auth_manager.register(
        username=username,
        email=f"{username}@example.com",
        password="PerfTest123!",
    )
    return username


class TestAuthPerformance:
    """Functional coverage of auth hot paths (no wall-clock assertions)."""

    def test_registration_round_trip(self, auth_manager):
        username = _register(auth_manager, "reg")
        user = auth_manager.get_user_by_username(username)
        assert user is not None
        assert user.username == username
        fetched = auth_manager.get_user(user.id)
        assert fetched is not None

    def test_login_round_trip(self, auth_manager):
        username = _register(auth_manager, "login")
        result = auth_manager.login(username, "PerfTest123!")
        assert result.token is not None

        token_info = auth_manager.verify_token(result.token)
        assert token_info.valid
        assert token_info.user_id == result.user.id

    def test_token_validation_bulk(self, auth_manager):
        username = _register(auth_manager, "bulk")
        results = [auth_manager.login(username, "PerfTest123!") for _ in range(10)]
        for r in results:
            token_info = auth_manager.verify_token(r.token)
            assert token_info.valid
            assert token_info.user_id == results[0].user.id

    def test_concurrent_logins(self, auth_manager):
        usernames = [_register(auth_manager, f"conc{i}") for i in range(6)]
        tokens = [None] * len(usernames)
        errors = []

        def worker(idx):
            try:
                r = auth_manager.login(usernames[idx], "PerfTest123!")
                tokens[idx] = r.token
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(i,)) for i in range(len(usernames))
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert all(t is not None for t in tokens)

    def test_session_management_under_load(self, auth_manager):
        username = _register(auth_manager, "sess")
        user = auth_manager.get_user_by_username(username)

        for _ in range(20):
            result = auth_manager.login(username, "PerfTest123!")
            assert result.token is not None

        sessions = auth_manager.get_sessions(user.id)
        # max_per_user is 10 in the test config; each login beyond the cap
        # evicts the oldest session, so 20 logins must land exactly at the cap.
        assert len(sessions) == 10

        # Eviction must actually work: revoking one session leaves 9 active.
        assert auth_manager.revoke_session(user.id, sessions[0].id) is True
        remaining = auth_manager.get_sessions(user.id)
        assert len(remaining) == 9
        assert all(s.id != sessions[0].id for s in remaining)

    def test_registration_memory_smoke(self, auth_manager):
        last_username = None
        for i in range(50):
            last_username = _register(auth_manager, f"mem{i}")
        user = auth_manager.get_user_by_username(last_username)
        assert user is not None
        assert user.username == last_username

    def test_repeated_registration_rejected(self, auth_manager):
        username = _register(auth_manager, "dup")
        from src.core.auth.exceptions import UserExistsError

        with pytest.raises(UserExistsError):
            auth_manager.register(
                username=username,
                email=f"dup_other_{uuid.uuid4().hex[:8]}@example.com",
                password="PerfTest123!",
            )
