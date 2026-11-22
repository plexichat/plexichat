"""Timing regression: evidence-URL safety checks must not be strictly sequential.

The enhanced-report handler resolves each client-supplied evidence URL with
``_is_safe_url_async`` (a DNS lookup offloaded to the threadpool). With up to
``MAX_EVIDENCE_URLS`` URLs this is an N-way I/O fan-out. Before the fix the
handler awaited each lookup one after another, so wall time scaled as
``N * latency``. After the fix the lookups run under a bounded semaphore, so
wall time scales as ``ceil(N / concurrency) * latency`` while fail-closed
behaviour (any unsafe URL rejects the whole report) is preserved.
"""

import asyncio
import time
from typing import Any, cast

import pytest

from src.api.routes.features.reports import (
    EnhancedReportRequest,
    MAX_EVIDENCE_URLS,
    submit_enhanced_report,
)


class _FakeUser:
    def __init__(self, user_id: int):
        self.user_id = user_id


class _FakeDB:
    def __init__(self):
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))
        return None

    def fetch_one(self, query, params=None):
        # The reported user exists; there is no pre-existing report.
        if "auth_users" in query:
            return (999,)
        return None


def _patch_common(monkeypatch, db):
    import src.api as api

    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(api, "get_messaging", lambda: None)
    monkeypatch.setattr(api, "get_servers", lambda: None)
    monkeypatch.setattr(
        "src.core.database.check_rate_limit", lambda *args, **kwargs: (True, 9)
    )


def _request(urls):
    return EnhancedReportRequest(
        target_type="user",
        target_id="999",
        reason="spam",
        evidence_urls=urls,
    )


# Per-lookup simulated DNS latency. N lookups sequential ~= N * LATENCY;
# bounded-concurrent ~= ceil(N / CONCURRENCY) * LATENCY.
_LATENCY = 0.05


def _patch_sleeping_checker(monkeypatch, unsafe=()):
    """Replace the real DNS checker with a fixed-latency async stub."""
    calls = []

    async def _check(url):
        calls.append(url)
        await asyncio.sleep(_LATENCY)
        return url not in unsafe

    monkeypatch.setattr("src.api.routes.features.reports._is_safe_url_async", _check)
    return calls


def test_evidence_url_checks_are_bounded_concurrent(monkeypatch):
    """N independent DNS lookups must overlap under a bounded semaphore.

    Fails before the fix: sequential time ~= N * _LATENCY.
    Passes after the fix: ~= ceil(N / concurrency) * _LATENCY.
    """
    db = _FakeDB()
    _patch_common(monkeypatch, db)
    n = MAX_EVIDENCE_URLS
    calls = _patch_sleeping_checker(monkeypatch)
    urls = [f"http://example{i}.com/evidence" for i in range(n)]

    start = time.perf_counter()
    result = asyncio.run(
        submit_enhanced_report(_request(urls), cast(Any, _FakeUser(1)))
    )
    elapsed = time.perf_counter() - start

    assert result["success"] is True
    assert sorted(calls) == sorted(urls)

    sequential = n * _LATENCY
    # Bounded fan-out (fixed concurrency >= 2) must be strictly faster than a
    # fully sequential run. Generous headroom keeps this deterministic on a
    # loaded CI worker while still failing the ~n*_LATENCY baseline.
    assert elapsed < sequential * 0.75, (
        f"evidence-URL checks look sequential: {elapsed:.3f}s for {n} lookups "
        f"(sequential baseline ~{sequential:.3f}s)"
    )


def test_evidence_url_all_safe_passes(monkeypatch):
    db = _FakeDB()
    _patch_common(monkeypatch, db)
    _patch_sleeping_checker(monkeypatch)
    urls = [f"http://example{i}.com/evidence" for i in range(MAX_EVIDENCE_URLS)]

    result = asyncio.run(
        submit_enhanced_report(_request(urls), cast(Any, _FakeUser(1)))
    )

    assert result["success"] is True


def test_evidence_url_unsafe_fails_closed(monkeypatch):
    db = _FakeDB()
    _patch_common(monkeypatch, db)
    urls = [f"http://example{i}.com/evidence" for i in range(MAX_EVIDENCE_URLS)]
    _patch_sleeping_checker(monkeypatch, unsafe={urls[2]})

    with pytest.raises(Exception) as exc:
        asyncio.run(submit_enhanced_report(_request(urls), cast(Any, _FakeUser(1))))

    error = cast(Any, exc.value)
    assert error.status_code == 400
    assert "Invalid evidence URL" in str(error.detail)
    # No INSERT should have happened for a rejected report.
    assert not any("INSERT INTO reports" in q for q, _ in db.executed)
