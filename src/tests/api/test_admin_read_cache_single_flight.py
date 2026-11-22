"""Single-flight for the expensive admin read render (cache stampede).

``read_cache.get_or_render`` renders the fully-serialized admin JSON bodies
(the role/permission catalog is the most expensive admin read).  Many admin
pollers hit the same key concurrently; on a cold cache every one of them used
to run the whole render (query + per-row serialization + jsonable_encoder)
independently -- a thundering herd on the exact structure the cache exists to
avoid recomputing.

These tests prove the measurable before/after with a call-count + barrier:
N concurrent cold callers must collapse to ~1 render, with identical results.
They also pin the two invariants that make coalescing safe: different keys
(the viewer/admin scope is part of the key) never share a computation, and a
failed render is shared as an error but never cached (fail closed).
"""

import threading
import time

import pytest

from src.core.admin import read_cache

pytestmark = pytest.mark.unit

_TLS = threading.local()


@pytest.fixture(autouse=True)
def _clean_cache():
    # Warm the lazy ``import src.api`` / db-token path so the first worker does
    # not pay import latency while the others are queued behind it.  Then drop
    # the warmup entry so every test starts from a genuinely cold cache.
    try:
        read_cache.get_or_render("sf_probe", ("__warmup__",), lambda: b"x")
    except Exception:
        pass
    read_cache.clear_all()
    yield
    read_cache.clear_all()


def _run_concurrent(keys, render):
    """Start ``keys`` callers together on ``get_or_render`` and join them all.

    Each thread tags itself with its key so ``render`` can produce a distinct
    body per key without taking arguments.
    """
    entered = []
    results = {}
    errors = []
    barrier = threading.Barrier(len(keys))

    def worker(key):
        entered.append(1)
        try:
            barrier.wait()
            _TLS.key = key
            results[key] = read_cache.get_or_render("sf_probe", (key,), render)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(k,), name=f"sfw-{k}") for k in keys
    ]
    for t in threads:
        t.start()
    deadline = time.monotonic() + 5
    while len(entered) < len(keys) and time.monotonic() < deadline:
        time.sleep(0.001)
    return threads, results, errors


def _settle(threads, gate):
    """Give every cold caller time to pile up before releasing the leader."""
    time.sleep(0.4)  # pre-fix: all N renders start; post-fix: only the leader
    gate.set()
    for t in threads:
        t.join(timeout=5)


def test_concurrent_cold_misses_collapse_to_one_render():
    """N concurrent cold callers cause ~1 expensive render, not N.

    Before the fix every caller runs ``render`` (each blocks on the gate until
    the test releases it), so ``len(renders) == N`` and the assertion fails.
    After the fix only the leader renders and the rest await its result.
    """
    renders = []
    gate = threading.Event()
    n_callers = 12

    def render():
        renders.append(1)
        gate.wait(timeout=5)  # hold the cold render open until everyone piled in
        return b"payload"

    threads, results, errors = _run_concurrent(["same-key"] * n_callers, render)
    _settle(threads, gate)

    assert errors == []
    assert renders == [1], f"expected one collapsed render, got {len(renders)}"
    assert results == {"same-key": b"payload"}


def test_coalescing_is_scoped_per_key():
    """Different keys (e.g. different admin ids) never share a computation."""
    renders = []
    gate = threading.Event()

    def render():
        key = getattr(_TLS, "key", "?")
        renders.append(key)
        gate.wait(timeout=5)
        return f"body-of-{key}".encode()

    threads, results, errors = _run_concurrent(["alice", "bob"], render)
    _settle(threads, gate)

    assert errors == []
    # Two distinct keys -> two independent renders, never one shared result.
    assert sorted(renders) == ["alice", "bob"]
    assert results == {
        "alice": b"body-of-alice",
        "bob": b"body-of-bob",
    }


def test_failed_render_is_shared_but_not_cached():
    """A cold failure propagates to waiters and is never cached (fail closed)."""
    renders = []
    gate = threading.Event()

    def render():
        renders.append(1)
        gate.wait(timeout=5)
        raise ValueError("render failed")

    threads, _, errors = _run_concurrent(["boom"] * 6, render)
    _settle(threads, gate)

    assert len(renders) == 1, "all waiters must share the leader's failed render"
    assert len(errors) == 6, "every waiter must observe the failure"
    assert all(isinstance(e, ValueError) for e in errors)
    assert len({id(e) for e in errors}) == 1, "waiters share the exact exception"

    # A failed compute is not cached: the next call retries the render.
    read_cache.get_or_render("sf_probe", ("boom",), lambda: b"recovered")
    assert read_cache.get_or_render(
        "sf_probe", ("boom",), lambda: (_ for _ in ()).throw(AssertionError("cached"))
    ) == b"recovered"
