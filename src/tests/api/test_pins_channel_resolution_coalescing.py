"""Per-request coalescing of the pins channel-resolution permission decision.

The pins route resolves the *same* ``get_channel(channel_id, user_id)`` twice on
every cache miss: once in the live access gate before the cache, and again in the
cached endpoint body to find the linked conversation. Each resolution pays a
Valkey channel-row GET, a DB membership query and the ``channels.view``
permission walk. The gate and body now share a request-local memo so the second
lookup is served from a per-request dict.

These tests pin four properties:

1. the gate + body pair performs ONE get_channel per request (measured before/
   after call counts);
2. the memo never crosses the request boundary and is keyed by the full
   (channel, user) identity -- no cross-request / cross-viewer leak;
3. a miss (``None``) is memoised too, so a DM id still resolves once;
4. an authoritative exception is never swallowed or turned into a grant.

Run with: pytest src/tests/api/test_pins_channel_resolution_coalescing.py -q -p no:xdist
"""

import sys
import types

import pytest

from src.api.routes.messages.messages_list import (
    _pins_channel_cache_key,
    _resolve_pins_channel,
)
from src.core.database.cache.request_local import request_cache_scope


CHANNEL = 777
OTHER_CHANNEL = 778
USER = 1
OTHER_USER = 2


@pytest.fixture(autouse=True)
def _no_asgi_context(monkeypatch):
    """Force the explicit request scope so tests are independent of ASGI state."""
    monkeypatch.setitem(
        sys.modules,
        "src.api.middleware.request_context",
        types.SimpleNamespace(current_context=lambda: None),
    )


class _Servers:
    """Counting stand-in for the servers module."""

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def get_channel(self, channel_id, user_id):
        self.calls.append((channel_id, user_id))
        if self.error is not None:
            raise self.error
        return self.result


# --- measured reduction: gate + body == one resolution -----------------------


def test_gate_and_body_share_one_resolution():
    channel = object()
    servers = _Servers(result=channel)

    with request_cache_scope():
        # The dependency runs first, then the cached endpoint body.
        gate = _resolve_pins_channel(servers, CHANNEL, USER)
        body = _resolve_pins_channel(servers, CHANNEL, USER)

    assert gate is channel and body is channel
    assert servers.calls == [(CHANNEL, USER)]


def test_perf_before_and_after_call_counts():
    """A request used to pay 2 get_channel calls; now it pays 1."""
    servers = _Servers(result=object())
    counts = {}

    # "before": no request-local memo, gate and body each call the resolver.
    counts["before"] = 0
    for _ in range(2):
        servers.get_channel(CHANNEL, USER)
        counts["before"] += 1

    # "after": same two-step request through the shared memo.
    servers.calls.clear()
    with request_cache_scope():
        _resolve_pins_channel(servers, CHANNEL, USER)
        _resolve_pins_channel(servers, CHANNEL, USER)
    counts["after"] = len(servers.calls)

    assert counts["before"] == 2
    assert counts["after"] == 1


# --- identity + scope isolation: no cross-request / cross-viewer leak --------


def test_no_cross_request_state():
    servers = _Servers(result=object())

    with request_cache_scope():
        _resolve_pins_channel(servers, CHANNEL, USER)
    with request_cache_scope():
        _resolve_pins_channel(servers, CHANNEL, USER)

    assert servers.calls == [(CHANNEL, USER), (CHANNEL, USER)]


@pytest.mark.asyncio
async def test_asgi_bound_context_shared_through_threadpool(monkeypatch):
    """The real middleware path (scope state + run_in_threadpool) coalesces too."""
    from starlette.concurrency import run_in_threadpool

    from src.api.middleware import request_context as rc

    # Emulate AuthenticationMiddleware binding a fresh per-request context.
    scope = {"type": "http", "state": {}}
    token = rc.bind_current_context(scope)
    monkeypatch.setitem(
        sys.modules,
        "src.api.middleware.request_context",
        types.SimpleNamespace(current_context=rc.current_context),
    )
    try:
        servers = _Servers(result=object())
        # The gate and the cached body each hop through the threadpool.
        gate = await run_in_threadpool(_resolve_pins_channel, servers, CHANNEL, USER)
        body = await run_in_threadpool(_resolve_pins_channel, servers, CHANNEL, USER)
        assert gate is body
        assert servers.calls == [(CHANNEL, USER)]
    finally:
        rc.reset_current_context(token)


def test_key_is_partitioned_by_channel_and_user():
    assert _pins_channel_cache_key(CHANNEL, USER) != _pins_channel_cache_key(
        OTHER_CHANNEL, USER
    )
    assert _pins_channel_cache_key(CHANNEL, USER) != _pins_channel_cache_key(
        CHANNEL, OTHER_USER
    )

    servers = _Servers(result=object())
    with request_cache_scope():
        _resolve_pins_channel(servers, CHANNEL, USER)
        _resolve_pins_channel(servers, OTHER_CHANNEL, USER)
        _resolve_pins_channel(servers, CHANNEL, OTHER_USER)
        _resolve_pins_channel(servers, CHANNEL, USER)

    assert servers.calls == [
        (CHANNEL, USER),
        (OTHER_CHANNEL, USER),
        (CHANNEL, OTHER_USER),
    ]


# --- fail-closed: misses and exceptions behave like the authoritative call ---


def test_miss_is_memoised_not_recomputed():
    servers = _Servers(result=None)

    with request_cache_scope():
        assert _resolve_pins_channel(servers, CHANNEL, USER) is None
        assert _resolve_pins_channel(servers, CHANNEL, USER) is None

    assert servers.calls == [(CHANNEL, USER)]


def test_authoritative_exception_propagates():
    boom = RuntimeError("channel store unavailable")
    servers = _Servers(error=boom)

    with request_cache_scope():
        with pytest.raises(RuntimeError) as exc:
            _resolve_pins_channel(servers, CHANNEL, USER)
        assert exc.value is boom
        # A failed authoritative lookup is not memoised as a grant (or a deny).
        with pytest.raises(RuntimeError):
            _resolve_pins_channel(servers, CHANNEL, USER)

    assert servers.calls == [(CHANNEL, USER), (CHANNEL, USER)]
