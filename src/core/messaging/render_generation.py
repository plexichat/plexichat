"""Per-conversation generation counters used to invalidate render caches.

The REST message list keeps a small in-process LRU of fully normalized render
payloads. A cached page must become stale the instant any message in its
conversation is created, edited or deleted. The messaging layer owns that
event (``MessageRepository.invalidate_conversation_cache``), so it bumps the
conversation's generation here; the API layer folds the generation into its
cache key. Keeping the counter in this neutral module avoids an import cycle
between core messaging and the API routes.

Boundedness
-----------
The registry is keyed by conversation id and was previously a bare ``dict``
that grew on every message write (and on every read via ``get``), for ever.
Under sustained traffic it is an unbounded memory leak: one entry per
conversation ever touched, never reclaimed even after a conversation is
deleted. It is now a hard-bounded, TTL'd LRU with BOTH an entry cap and an
approximate byte cap.

Correctness across eviction
---------------------------
Naively dropping a generation entry is dangerous: a conversation whose entry
is evicted could have its generation revert to an earlier value, reviving a
pre-write page still alive in the short-lived render cache. Two rules make
eviction safe:

* ``bump`` draws its value from a process-wide monotonic counter. A generation
  value is never reused (until the test-only ``clear``), so a freshly bumped
  conversation can never collide with a page cached before the eviction.
* ``get`` on a missing/expired key binds that conversation to the CURRENT
  high-water mark of the counter (0 in a fresh process). That value is >= every
  generation previously handed out, so it cannot match a stale page, and it is
  stable for later reads so the render LRU still gets normal hit rates.

Hot-path cost
-------------
``bump`` runs on every message create/edit/delete/ack. The TTL expiry sweep is
an O(n) pass over the whole registry (up to ``_MAX_ENTRIES`` entries), so paying
it on every write made a hot path O(n). The entry/byte caps stay hard and are
still enforced on every write; only the TTL sweep is amortised to at most once
per ``min(_TTL_SECONDS, _SWEEP_INTERVAL_CAP_SECONDS)``. This never delays
*serving* a correct generation: ``get`` drops an expired entry for the key it
is asked about, and ``bump`` always writes a fresh generation. A short TTL
(tests, short-TTL deployments) keeps its exact sweep semantics.
"""

import sys
import threading
import time
from collections import OrderedDict
from typing import Any, Dict

# Hard bounds. 50k small integer entries are only a few MB, but an unbounded
# registry would grow for the entire process lifetime. The byte cap is a
# backstop for unusually large (e.g. string) keys.
_MAX_ENTRIES = 50_000
_MAX_BYTES = 16 * 1024 * 1024
_TTL_SECONDS = 24 * 60 * 60  # reclaim untouched conversations eventually

# The TTL sweep is an O(n) pass over the registry. With a 24h TTL there is no
# benefit to paying that cost on every message write, so cap its frequency.
# Reclaiming an expired-but-unaccessed entry may lag by up to this long;
# serving one never does (get() drops the requested key's expired entry).
_SWEEP_INTERVAL_CAP_SECONDS = 60.0

# Rough per-entry container overhead (OrderedDict node + tuples) added to the
# measured key/value sizes when tracking approximate retained bytes.
_NODE_OVERHEAD = 96

_lock = threading.Lock()
# key -> (generation, stored_at_monotonic, approx_bytes)
_generations: "OrderedDict[Any, tuple]" = OrderedDict()
_next_generation = 0
_total_bytes = 0
_last_sweep_at = 0.0
_sweeps = 0


def _normalize(conversation_id: Any) -> Any:
    try:
        return int(conversation_id)
    except (TypeError, ValueError):
        return conversation_id


def _entry_bytes(key: Any, generation: int, stored_at: float) -> int:
    """Approximate retained bytes for one registry entry."""
    try:
        return (
            sys.getsizeof(key)
            + sys.getsizeof(generation)
            + sys.getsizeof(stored_at)
            + _NODE_OVERHEAD
        )
    except Exception:  # pragma: no cover - sys.getsizeof is reliable for these
        return _NODE_OVERHEAD + 128


def _drop_locked(key: Any) -> None:
    """Remove ``key`` from the registry, maintaining the byte counter."""
    global _total_bytes
    old = _generations.pop(key, None)
    if old is not None:
        _total_bytes -= old[2]
        if _total_bytes < 0:  # defensive: external mutation / miscount
            _total_bytes = 0


def _prune_and_evict_locked(now: float) -> None:
    """Expire stale entries, then enforce the entry and byte caps (LRU).

    The entry/byte caps are hard and are enforced on every write. The TTL
    sweep is O(n), so with a long TTL it is amortised to at most once per
    ``min(_TTL_SECONDS, _SWEEP_INTERVAL_CAP_SECONDS)``. A short TTL keeps its
    exact semantics; a long TTL only delays *reclaiming* an expired entry,
    never serving one -- get() still drops an expired entry on access and
    bump() always writes a fresh generation.
    """
    global _total_bytes, _last_sweep_at, _sweeps
    if _TTL_SECONDS > 0:
        interval = min(_TTL_SECONDS, _SWEEP_INTERVAL_CAP_SECONDS)
        if now - _last_sweep_at >= interval:
            _last_sweep_at = now
            _sweeps += 1
            expired = [
                key
                for key, (_, stored_at, _) in _generations.items()
                if now - stored_at > _TTL_SECONDS
            ]
            for key in expired:
                _drop_locked(key)

    while _generations and (
        len(_generations) > _MAX_ENTRIES or _total_bytes > _MAX_BYTES
    ):
        key, (_, _, size) = _generations.popitem(last=False)
        _total_bytes -= size
    if _total_bytes < 0:  # defensive
        _total_bytes = 0


def bump(conversation_id: Any) -> int:
    """Advance and return the generation for ``conversation_id``.

    The returned value is drawn from a monotonically increasing process-wide
    counter, so it is never equal to a generation previously observed by any
    caller. That is what makes render-cache invalidation survive an eviction.
    """
    global _next_generation, _total_bytes
    key = _normalize(conversation_id)
    now = time.monotonic()
    with _lock:
        _next_generation += 1
        generation = _next_generation
        _drop_locked(key)
        size = _entry_bytes(key, generation, now)
        _generations[key] = (generation, now, size)
        _generations.move_to_end(key)
        _total_bytes += size
        _prune_and_evict_locked(now)
        return generation


def get(conversation_id: Any) -> int:
    """Return the generation for ``conversation_id``.

    A conversation that has never been seen is bound to the current high-water
    mark (0 in a fresh process). Missing/expired entries are re-bound to that
    same high-water mark so a stale page from before an eviction can never be
    matched again, while repeated reads keep a stable value and retain normal
    cache-hit behaviour.
    """
    global _total_bytes
    key = _normalize(conversation_id)
    now = time.monotonic()
    with _lock:
        entry = _generations.get(key)
        if entry is not None:
            generation, stored_at, _ = entry
            if _TTL_SECONDS > 0 and now - stored_at > _TTL_SECONDS:
                _drop_locked(key)
            else:
                _generations.move_to_end(key)
                return generation

        generation = _next_generation
        size = _entry_bytes(key, generation, now)
        _generations[key] = (generation, now, size)
        _generations.move_to_end(key)
        _total_bytes += size
        _prune_and_evict_locked(now)
        return generation


def clear() -> None:
    """Reset all counters. Test helper only."""
    global _next_generation, _total_bytes, _last_sweep_at, _sweeps
    with _lock:
        _generations.clear()
        _total_bytes = 0
        _next_generation = 0
        _last_sweep_at = 0.0
        _sweeps = 0


def stats() -> Dict[str, int]:
    """Observability into the bounded registry (caps, size, bytes, high-water)."""
    with _lock:
        return {
            "entries": len(_generations),
            "max_entries": _MAX_ENTRIES,
            "bytes": _total_bytes,
            "max_bytes": _MAX_BYTES,
            "high_water": _next_generation,
            "sweeps": _sweeps,
        }
