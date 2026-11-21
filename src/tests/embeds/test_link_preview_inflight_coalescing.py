"""Concurrent identical URL previews resolve once, not once per request.

The resolution cache absorbs *sequential* repeats, but a burst of requests for
the same cold URL (a link posted to a busy channel) used to make every caller
pay for the full remote fetch + parse + image proxy. These tests pin the bounded
in-flight coalescing that removes that duplicate work while proving the
security gates still run for every caller.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import Mock

import pytest

from src.core.embeds.link_preview import LinkPreviewService, PreviewMetadata

URL = "https://example.com/page"
HTML = b"<html><head><title>T</title></head></html>"


def _miss_db():
    db = Mock()
    db.fetch_one.return_value = None
    db.execute.return_value.rowcount = 1
    return db


def _service():
    service = LinkPreviewService(_miss_db())
    # The real SSRF gate is covered exhaustively in test_link_preview_security;
    # stub only its network part and count that it still runs per request.
    service._validate_url = Mock(
        side_effect=lambda url: ("example.com", "93.184.216.34", url)
    )
    return service


def _run_concurrently(service, calls, count):
    """Start ``count`` callers at once, collect results/errors, join them."""
    barrier = threading.Barrier(count)
    results = []
    errors = []

    def worker(i):
        try:
            barrier.wait()
            results.append(service.generate_preview(i, calls[i]))
        except BaseException as exc:  # surface thread failures in assertions
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    return threads, results, errors


def test_concurrent_identical_previews_resolve_once():
    """Six simultaneous callers for one cold URL pay for one remote fetch."""
    service = _service()
    consume = Mock(return_value=True)
    service._consume_rate_limit = consume

    started = threading.Event()
    release = threading.Event()
    fetches = []

    def slow_fetch(url, hostname=None, resolved_ip=None):
        fetches.append(url)
        started.set()
        # Hold the leader in its fetch until the other callers have joined the
        # in-flight flight; without coalescing each of them would fetch too.
        release.wait(timeout=2)
        return HTML, url, "text/html"

    service._fetch_with_redirects = Mock(side_effect=slow_fetch)

    count = 6
    threads, results, errors = _run_concurrently(service, [URL] * count, count)
    assert started.wait(timeout=2), "leader fetch never started"
    time.sleep(0.1)
    release.set()
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert len(fetches) == 1, "same preview derivative was generated per caller"
    assert len(results) == count
    assert all(
        isinstance(meta, PreviewMetadata) and meta.title == "T" for meta in results
    )

    # Per-request security gates are NOT weakened by coalescing.
    assert service._validate_url.call_count == count
    assert consume.call_count == count
    assert {call.args[0] for call in consume.call_args_list} == set(range(count))

    stats = service.resolution_inflight_stats()
    assert stats["led"] == 1
    assert stats["joined"] == count - 1
    assert stats["inflight"] == 0  # all flights cleaned up


def test_coalesced_failure_is_shared_but_never_cached():
    """Waiters share a leader's failure, and a later request still retries."""
    service = _service()
    fetches = []
    started = threading.Event()
    release = threading.Event()

    def failing_fetch(url, hostname=None, resolved_ip=None):
        fetches.append(url)
        started.set()
        # Hold the failing leader so the other callers actually join its flight
        # before it publishes the shared error.
        release.wait(timeout=2)
        raise ValueError("upstream down")

    service._fetch_with_redirects = Mock(side_effect=failing_fetch)

    count = 4
    threads, _results, errors = _run_concurrently(service, [URL] * count, count)
    assert started.wait(timeout=2)
    time.sleep(0.1)
    release.set()
    for thread in threads:
        thread.join(timeout=5)

    assert len(errors) == count
    assert all("upstream down" in str(exc) for exc in errors)
    assert len(fetches) == 1  # one fetch shared by all waiters, no retry storm

    # Failures are not memoized: the next request performs a fresh fetch.
    service._fetch_with_redirects = Mock(
        side_effect=lambda url, hostname=None, resolved_ip=None: (
            HTML,
            url,
            "text/html",
        )
    )
    assert service.generate_preview(99, URL).title == "T"
    assert service.resolution_inflight_stats()["inflight"] == 0


def test_inflight_registry_is_bounded():
    """A flood of distinct URLs cannot pin unbounded waiter state."""
    service = _service()
    service._max_inflight_resolutions = 2

    release = threading.Event()

    def slow_fetch(url, hostname=None, resolved_ip=None):
        release.wait(timeout=2)
        return HTML, url, "text/html"

    service._fetch_with_redirects = Mock(side_effect=slow_fetch)

    count = 6
    urls = [f"https://example.com/page{i}" for i in range(count)]
    threads, results, errors = _run_concurrently(service, urls, count)
    time.sleep(0.1)
    release.set()
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert len(results) == count
    stats = service.resolution_inflight_stats()
    assert stats["peak"] <= 2
    assert stats["rejected"] == count - 2
    assert stats["inflight"] == 0


@pytest.mark.parametrize("skip_cache", [False, True])
def test_returned_metadata_is_detached_from_shared_flight(skip_cache):
    """A caller mutating its result cannot poison another's resolution."""
    service = _service()
    service._fetch_with_redirects = Mock(
        side_effect=lambda url, hostname=None, resolved_ip=None: (
            HTML,
            url,
            "text/html",
        )
    )
    first = service.generate_preview(1, URL, skip_cache=skip_cache)
    first.title = "ATTACKER"
    second = service.generate_preview(2, URL)
    assert second.title == "T"
