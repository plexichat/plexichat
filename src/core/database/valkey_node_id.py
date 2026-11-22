"""Valkey-backed unique Snowflake node-id leases.

Under ``docker compose up --scale backend=N`` every replica runs the same
image with the same environment, so the Snowflake ``(datacenter, worker)``
(or 10-bit machine id) cannot be pinned per container by compose env alone.
Deriving it from a hostname hash is only *probabilistic*: a 10-bit hash space
means a ~0.6% chance of a collision with four replicas and ~40% with 32. A
failed ``gethostname()`` additionally used to fall back to a constant string,
which guarantees a collision when the container PID is constant too.

This module removes that guesswork. Live workers claim a unique 10-bit node
id from a shared Valkey instance using an atomic ``SET NX`` lease, so every
replica is guaranteed a distinct identity. The lease carries a TTL and is
refreshed in the background; it auto-expires if a worker dies, and is released
cleanly at process exit.

Explicitly configured ids (``PLEXICHAT_WORKER_ID`` /
``PLEXICHAT_DATACENTER_ID`` / ``PLEXICHAT_MACHINE_ID`` or config values) take
precedence and are never overwritten: the caller decides whether coordination
is needed.

Scaling notes
-------------
* Leases are refreshed by ONE shared background thread for the whole process
  instead of a thread per node id. The old design spawned a dedicated daemon
  thread per lease, each doing its own tight GET-then-EXPIRE poll. With N live
  leases that meant N threads and 2*N round trips per refresh tick.
* Each refresh is a single atomic ``EVAL`` round trip (compare token, bump
  TTL) when the client supports it, falling back to GET + EXPIRE otherwise.
* The shared thread sleeps until the nearest lease is due (with per-lease
  jitter) rather than polling on a fixed tick, so idle workers do no work and
  replicas do not all refresh in lockstep (no thundering herd).

Uniqueness is preserved: ``SET NX`` still gives a single winner per slot, the
token check still detects a lost/stolen lease before it is refreshed, and a
lost lease is deactivated (never silently reused).
"""

import atexit
import hashlib
import os
import random
import socket
import threading
import time
import uuid
from typing import Dict, Optional

import utils.logger as logger

LEASE_PREFIX = "snowflake:node-lease:"
DEFAULT_LEASE_TTL = 300  # seconds
NODE_ID_SPACE = 1024  # 10 bits: 5-bit datacenter + 5-bit worker

# Fraction of the refresh interval used to spread refreshes across leases and
# replicas. Refresh always happens before ``ttl`` elapses, so a lease can never
# expire underneath a live worker (which would let another worker claim it).
_JITTER_FRACTION = 0.2

# Atomically verify ownership and refresh the TTL in ONE round trip. Returns 1
# when the token still matches (and the TTL was bumped), 0 when the lease was
# lost. ``eval_lua`` prefixes KEYS consistently with the other Valkey commands.
_REFRESH_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('expire', KEYS[1], ARGV[2]) "
    "else return 0 end"
)

# Process-wide refresh state. A single daemon thread refreshes every live
# lease; leases register on start() and unregister on release().
_refresh_lock = threading.Lock()
_live_leases: Dict["NodeIdLease", float] = {}  # lease -> monotonic next-due time
_refresh_wake = threading.Event()
_refresh_stop = threading.Event()
_refresh_thread: Optional[threading.Thread] = None


def _refresh_interval(ttl: int) -> float:
    """Base seconds between refreshes (kept well under the lease TTL)."""
    return max(1, ttl // 3)


def _next_due(lease: "NodeIdLease") -> float:
    """Schedule the next refresh with jitter, never later than one interval."""
    base = _refresh_interval(lease.ttl)
    jitter = random.uniform(0.0, base * _JITTER_FRACTION)
    return time.monotonic() + base - jitter


def refresh_lease(lease: "NodeIdLease") -> str:
    """Refresh one lease. Return ``"ok"``, ``"lost"`` or ``"error"``.

    Uses an atomic compare-and-expire script when the client exposes
    ``eval_lua`` so the token check and TTL bump cost ONE round trip instead
    of the old GET + EXPIRE pair. Lightweight clients (and tests) fall back to
    GET + EXPIRE. ``"lost"`` means the token no longer matches (another worker
    owns the slot); ``"error"`` is a transient failure and must be retried.
    """
    client = lease.client
    eval_lua = getattr(client, "eval_lua", None)
    if callable(eval_lua):
        try:
            result = eval_lua(_REFRESH_LUA, [lease.key], [lease.token, lease.ttl])
        except Exception as exc:
            logger.warning("Could not refresh Snowflake node-id lease: %s", exc)
            return "error"
        return "ok" if result else "lost"

    try:
        if client.get(lease.key) != lease.token:
            return "lost"
        client.expire(lease.key, lease.ttl)
        return "ok"
    except Exception as exc:
        logger.warning("Could not refresh Snowflake node-id lease: %s", exc)
        return "error"


def _refresher_loop() -> None:
    """Single batched, throttled refresh loop for every live lease."""
    while not _refresh_stop.is_set():
        _refresh_wake.clear()
        with _refresh_lock:
            now = time.monotonic()
            due = [lease_id for lease_id, t in _live_leases.items() if t <= now]
            wait = min((t - now for t in _live_leases.values()), default=None)

        for lease in due:
            with _refresh_lock:
                if lease not in _live_leases or not lease._active:
                    continue
            outcome = refresh_lease(lease)
            with _refresh_lock:
                if not lease._active or lease not in _live_leases:
                    continue
                if outcome == "lost":
                    lease._active = False
                    del _live_leases[lease]
                    logger.error(
                        "Lost Valkey lease for Snowflake node id %d; IDs may "
                        "collide until this worker restarts",
                        lease.node_id,
                    )
                else:
                    # "ok" and transient "error" both retry on the next tick;
                    # the TTL left by the last successful refresh covers a
                    # missed refresh.
                    _live_leases[lease] = _next_due(lease)

        if wait is None:
            # No leases (or all scheduled during the work above): park until a
            # lease registers or the thread is told to stop.
            _refresh_wake.wait()
        else:
            _refresh_wake.wait(max(0.01, wait))


def _ensure_refresher_running() -> None:
    global _refresh_thread
    with _refresh_lock:
        if _refresh_thread is not None and _refresh_thread.is_alive():
            return
        _refresh_stop.clear()
        _refresh_thread = threading.Thread(
            target=_refresher_loop,
            name="snowflake-node-id-lease",
            daemon=True,
        )
        _refresh_thread.start()


def _register(lease: "NodeIdLease") -> None:
    with _refresh_lock:
        _live_leases[lease] = _next_due(lease)
    _ensure_refresher_running()
    _refresh_wake.set()


def _unregister(lease: "NodeIdLease") -> None:
    with _refresh_lock:
        _live_leases.pop(lease, None)
    _refresh_wake.set()


class NodeIdLease:
    """A claimed Snowflake node id, kept alive by a TTL lease in Valkey."""

    def __init__(
        self,
        client,
        node_id: int,
        key: str,
        token: str,
        ttl: int,
        start_refresh: bool = True,
    ):
        self.client = client
        self.node_id = node_id
        self.key = key
        self.token = token
        self.ttl = ttl
        self._stop = threading.Event()
        self._active = True
        # Retained for API compatibility; refreshing is now shared, not local.
        self._thread = None
        # Release on clean interpreter shutdown even if the caller forgets.
        atexit.register(self.release)
        if start_refresh:
            self.start()

    def start(self) -> None:
        """Register with the shared refresher (idempotent)."""
        if not self._active:
            return
        _register(self)

    def release(self) -> None:
        """Release the claim so another worker can take the slot."""
        self._stop.set()
        self._active = False
        _unregister(self)
        try:
            if self.client.get(self.key) == self.token:
                self.client.delete(self.key)
        except Exception as exc:
            logger.debug("Could not release Snowflake node-id lease: %s", exc)


def _default_start() -> int:
    """Spread initial claim attempts by hostname so workers don't all hit 0."""
    try:
        hostname = socket.gethostname()
    except Exception:
        hostname = os.urandom(16).hex()
    return int.from_bytes(hashlib.sha256(hostname.encode()).digest()[:2], "big") & (
        NODE_ID_SPACE - 1
    )


def claim_node_id(
    client,
    ttl: int = DEFAULT_LEASE_TTL,
    start: int | None = None,
    start_refresh: bool = True,
) -> NodeIdLease:
    """Atomically claim a unique 10-bit Snowflake node id from Valkey.

    ``client`` must provide ``set_nx(key, value, ttl)``, ``get``, ``expire``
    and ``delete`` (the project's ``ValkeyClient`` does). ``eval_lua`` is used
    for a single-round-trip refresh when available. Because ``SET NX`` is
    atomic, concurrent callers can never win the same slot.

    Raises ``RuntimeError`` when all 1024 slots are already leased.
    """
    token = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"
    if start is None:
        start = _default_start()
    for offset in range(NODE_ID_SPACE):
        node_id = (start + offset) % NODE_ID_SPACE
        key = f"{LEASE_PREFIX}{node_id}"
        if client.set_nx(key, token, ttl=ttl):
            lease = NodeIdLease(client, node_id, key, token, ttl, start_refresh)
            logger.info("Claimed Snowflake node id %d via Valkey", node_id)
            return lease
    raise RuntimeError("No free Snowflake node id available: all 1024 slots are leased")
