"""Regression: the derived security-policy cache is warmed at boot, off-request.

``get_static_policy()`` is the first call the authentication middleware makes on
every HTTP request. On a cold process that call builds the whole policy (config
reads, prefix derivation, docs-path resolution) on the request path. Boot
warm-up now primes that single, request-invariant snapshot on a worker thread
so the first real request is a cache hit. These tests pin the warm-up wiring,
the cold/warm cost, and -- critically -- that every auth gate still runs on a
warmed policy (the cache only holds the shared policy, never an auth decision).
"""

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.api import warmup
from src.api.middleware import policy_cache


@pytest.fixture(autouse=True)
def _cold_policy_cache():
    policy_cache.clear_static_policy_cache()
    yield
    policy_cache.clear_static_policy_cache()


def _counting_loader(delay=0.0):
    """A deterministic stand-in for the cold policy build with a known cost."""
    calls = []
    value = policy_cache.StaticPolicy(
        api_prefix="/api/v1",
        public_endpoints=frozenset({"/api/v1/status"}),
        docs_path="/docs/api",
        password_requirements_prefix="/api/v1/auth/password-requirements",
        webhook_execute_prefix="/api/v1/webhooks/",
    )

    def loader():
        calls.append(1)
        if delay:
            time.sleep(delay)
        return value

    return calls, loader


def test_warm_static_policy_cache_primes_snapshot(monkeypatch):
    calls, loader = _counting_loader()
    monkeypatch.setattr(policy_cache._policy_snapshot, "_loader", loader)

    policy_cache.warm_static_policy_cache()
    assert len(calls) == 1

    # Subsequent reads are cache hits; the expensive builder does not rerun.
    assert policy_cache.get_static_policy().api_prefix == "/api/v1"
    assert policy_cache.get_static_policy().api_prefix == "/api/v1"
    assert calls == [1]


def test_cold_first_hit_costs_the_build_and_warm_hit_skips_it(monkeypatch):
    cold_cost = 0.03  # stand-in for the config/prefix/docs-path build
    calls, loader = _counting_loader(delay=cold_cost)
    monkeypatch.setattr(policy_cache._policy_snapshot, "_loader", loader)

    # Cold path: the first read pays the full build.
    t0 = time.perf_counter()
    policy_cache.get_static_policy()
    cold_elapsed = time.perf_counter() - t0

    # Warm the cache off-request, then time a read that must be a hit.
    policy_cache.clear_static_policy_cache()
    policy_cache.warm_static_policy_cache()
    t0 = time.perf_counter()
    policy_cache.get_static_policy()
    warm_elapsed = time.perf_counter() - t0

    assert cold_elapsed >= cold_cost
    assert warm_elapsed < cold_cost / 3
    assert warm_elapsed < cold_elapsed / 3
    assert len(calls) == 2  # one cold build + one warm-up build, no third build


def test_security_policy_warmup_is_idempotent(monkeypatch):
    calls, loader = _counting_loader()
    monkeypatch.setattr(policy_cache._policy_snapshot, "_loader", loader)

    policy_cache.warm_static_policy_cache()
    policy_cache.warm_static_policy_cache()

    assert calls == [1]


def test_security_policy_warmup_is_non_fatal(monkeypatch):
    def boom():
        raise RuntimeError("policy build exploded")

    monkeypatch.setattr(policy_cache._policy_snapshot, "_loader", boom)

    # A broken build must be logged and swallowed, never raised into startup.
    policy_cache.warm_static_policy_cache()


def test_warm_once_invokes_security_policy_warmup(monkeypatch):
    called = []
    monkeypatch.setattr(warmup, "_warm_security_policy", lambda: called.append(True))

    assert warmup.warm_once() >= 0.0
    assert called == [True]


def test_policy_warmup_runs_off_the_event_loop_thread(monkeypatch):
    monkeypatch.setattr(warmup, "_warmup_enabled", lambda: True)
    monkeypatch.setattr(warmup, "_warmup_delay", lambda: 0.0)
    seen = {}

    def record():
        seen["thread"] = threading.current_thread()

    monkeypatch.setattr(warmup, "_warm_security_policy", record)
    asyncio.run(warmup.warmup_loop())

    assert "thread" in seen
    assert seen["thread"] is not threading.main_thread()


def _drive_auth_middleware(path):
    """Run AuthenticationMiddleware once with no credentials; return the result."""
    from src.api.middleware.authentication import AuthenticationMiddleware

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    messages = []
    reached = []

    async def send(message):
        messages.append(message)

    async def inner(scope, receive_, send_):
        reached.append(True)
        await send_({"type": "http.response.start", "status": 200, "headers": []})
        await send_({"type": "http.response.body", "body": b"ok"})

    fake_auth = MagicMock()
    fake_auth.is_api_access_token_required.return_value = True
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "query_string": b"",
        "client": ("testclient", 50000),
        "scheme": "http",
        "server": ("testserver", 80),
    }

    with (
        patch("src.api.is_self_test_request", return_value=False),
        patch("src.api.get_auth", return_value=fake_auth),
        patch("src.api.get_db", return_value=None),
    ):
        asyncio.run(AuthenticationMiddleware(inner)(scope, receive, send))

    start = next(m for m in messages if m["type"] == "http.response.start")
    return start["status"], reached, fake_auth


def test_first_request_skips_cold_build_but_auth_gate_still_runs(monkeypatch):
    calls, loader = _counting_loader()
    monkeypatch.setattr(policy_cache._policy_snapshot, "_loader", loader)

    # Prime through the boot warm-up path, not through a request.
    policy_cache.warm_static_policy_cache()
    assert calls == [1]

    # A protected path with no access token must still be rejected by the gate.
    status, reached, fake_auth = _drive_auth_middleware("/api/v1/users/@me")

    # The first real request hit the warmed cache; no cold re-evaluation.
    assert calls == [1]
    # ...but the auth gate still ran: the protected path was denied with 401
    # and the downstream app was never reached.
    assert status == 401
    assert reached == []
    assert fake_auth.is_api_access_token_required.called


def test_warmed_public_endpoints_still_bypass_auth(monkeypatch):
    """Warming must not move a public endpoint behind the auth gate."""
    calls, loader = _counting_loader()
    monkeypatch.setattr(policy_cache._policy_snapshot, "_loader", loader)
    policy_cache.warm_static_policy_cache()

    # "/api/v1/status" is public in the warmed policy, so the gate lets it
    # through to the downstream app unchanged.
    status, reached, fake_auth = _drive_auth_middleware("/api/v1/status")

    assert calls == [1]
    assert status == 200
    assert reached == [True]
    assert not fake_auth.is_api_access_token_required.called
