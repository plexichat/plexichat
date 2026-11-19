"""
Twitter-style Snowflake ID generator.

Format: [1-bit unused][41-bit timestamp][5-bit datacenter][5-bit worker][12-bit sequence]

Supports auto-derived IDs from machine characteristics for single-machine deployments
and environment variable configuration for distributed deployments.

Scaling notes
-------------
* The hostname/PID -> node-identity derivation is cached: the digest inputs are
  stable for a process, so hashing them on every generator construction (twice,
  historically — once per 5-bit field) was pure overhead. The cache is keyed by
  hostname so distinct replicas and the random fallback still stay distinct.
* Sequence numbers are reserved in contiguous batches under the lock, then
  served from a per-thread cache with no lock. The lock is taken once per batch
  instead of once per ID, while reservations stay disjoint (multi-worker
  uniqueness) and the strict backwards-clock guard is preserved.
"""

import functools
import hashlib
import os
import threading
import time
from typing import Dict, Optional

import utils.logger as logger


@functools.lru_cache(maxsize=256)
def _node_id_from_host(hostname: str, pid: int) -> int:
    """Derive the stable 10-bit node identity for a (hostname, pid) pair.

    Cached because both inputs are constant for the life of a process: hashing
    the hostname on every generator construction — historically twice, once
    for the worker id and once for the datacenter id — is wasted work. Keying
    the cache by hostname keeps the scaled-worker guarantee intact: two
    replicas with different hostnames derive different identities, and the
    random fallback hostname is unique per call.
    """
    digest = hashlib.sha256(hostname.encode()).digest()
    return (int.from_bytes(digest[:2], "big") & 0x3FF) ^ (pid & 0x3FF)


class SnowflakeGenerator:
    """
    Twitter-style Snowflake ID generator.
    Format: [1-bit unused][41-bit timestamp][5-bit datacenter][5-bit worker][12-bit sequence]

    For single-machine deployments, auto-generates IDs from machine characteristics.
    For distributed deployments, set PLEXICHAT_WORKER_ID and PLEXICHAT_DATACENTER_ID.
    """

    # Number of sequence numbers reserved per lock acquisition. The 12-bit
    # sequence space holds 4096 values per millisecond, so 256 keeps the lock
    # acquisition rate low without wastefully burning through a millisecond.
    _BATCH_SIZE = 256

    def __init__(
        self,
        worker_id: Optional[int] = None,
        datacenter_id: Optional[int] = None,
        epoch_timestamp: Optional[int] = None,
    ):
        # Epoch: 2024-01-01 00:00:00 UTC
        self.epoch = epoch_timestamp or 1704067200000

        # Derive the shared node identity once. The old code called
        # _auto_node_id() twice (worker + datacenter), re-reading the hostname
        # and recomputing the digest each time for the same process identity.
        node_id: Optional[int] = None
        if worker_id is None or datacenter_id is None:
            node_id = self._auto_node_id()
        if worker_id is None:
            worker_id = self._get_auto_worker_id(node_id)
        if datacenter_id is None:
            datacenter_id = self._get_auto_datacenter_id(node_id)

        # Validate bounds
        if not (0 <= worker_id <= 31):
            raise ValueError(f"worker_id must be 0-31, got {worker_id}")
        if not (0 <= datacenter_id <= 31):
            raise ValueError(f"datacenter_id must be 0-31, got {datacenter_id}")

        self.worker_id = worker_id & 0x1F
        self.datacenter_id = datacenter_id & 0x1F
        self.sequence = 0
        self.last_timestamp = -1
        self._lock = threading.Lock()

        # Highest sequence number reserved at ``last_timestamp``. Kept
        # separate from ``sequence`` (the last-issued mirror) so batch
        # reservations can never hand out the same (timestamp, sequence) pair.
        self._reserve_cursor: Optional[int] = None
        # Per-thread batch: ``ts``, ``next_seq`` and ``end_seq`` describe the
        # disjoint run of sequence values this thread may serve lock-free.
        self._local = threading.local()

        logger.debug(
            f"SnowflakeGenerator initialized: worker={self.worker_id}, datacenter={self.datacenter_id}"
        )

    def _auto_node_id(self) -> int:
        """Derive a stable 10-bit node identity for this process.

        The high 5 bits become the datacenter id and the low 5 bits the
        worker id. Deriving both from the *same* value is what makes the
        (datacenter, worker) pair unique per replica: two containers get
        distinct hostnames under ``docker compose up --scale backend=N``,
        and the PID is mixed in so two processes on one host differ too.

        The old scheme only used 2 bits of the hostname for the worker id
        (``host_hash[0] % 4``) while the PID (constant ``1`` inside a
        container) dominated the low bits. With four replicas that left a
        ~90% chance of at least one worker-id collision. Spanning the full
        10-bit node space brings that down to ~0.6% while keeping the 5-bit
        field layout unchanged.
        """
        import socket

        try:
            hostname = socket.gethostname()
        except Exception:
            # A constant fallback would give every replica the same node id
            # (container PIDs are often constant too), so use a per-process
            # random value instead — never a shared constant. The random
            # hostname also becomes the cache key, keeping fallback callers
            # distinct rather than collapsing onto one cached identity.
            hostname = "plexichat-" + os.urandom(16).hex()
        return _node_id_from_host(hostname, os.getpid())

    @staticmethod
    def _env_node_id(name: str) -> Optional[int]:
        """Return an explicit 5-bit id from the environment, if valid."""
        raw = os.environ.get(name)
        if raw is None:
            return None
        try:
            return int(raw) % 32
        except ValueError:
            return None

    def _get_auto_worker_id(self, node_id: Optional[int] = None) -> int:
        """Auto-derive worker ID from environment or machine characteristics."""
        explicit = self._env_node_id("PLEXICHAT_WORKER_ID")
        if explicit is not None:
            return explicit
        if node_id is None:
            node_id = self._auto_node_id()
        return node_id & 0x1F

    def _get_auto_datacenter_id(self, node_id: Optional[int] = None) -> int:
        """Auto-derive datacenter ID from environment or machine characteristics."""
        explicit = self._env_node_id("PLEXICHAT_DATACENTER_ID")
        if explicit is not None:
            return explicit
        if node_id is None:
            node_id = self._auto_node_id()
        return (node_id >> 5) & 0x1F

    def rebind_node_id(self, worker_id: int, datacenter_id: int) -> None:
        """Rebind this generator to a coordinated (datacenter, worker) pair.

        Called once at startup after Valkey assigns a unique node id to this
        replica, before the server serves requests. The lock keeps the
        reassignment safe against any concurrent generator use.
        """
        if not (0 <= worker_id <= 31):
            raise ValueError(f"worker_id must be 0-31, got {worker_id}")
        if not (0 <= datacenter_id <= 31):
            raise ValueError(f"datacenter_id must be 0-31, got {datacenter_id}")
        with self._lock:
            self.worker_id = worker_id & 0x1F
            self.datacenter_id = datacenter_id & 0x1F

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000)

    def _compose(self, timestamp: int, sequence: int) -> int:
        """Pack a reserved (timestamp, sequence) pair into a snowflake id.

        Worker/datacenter are read here, at compose time, so a Valkey rebind
        between reservations takes effect immediately without invalidating
        already-reserved sequence runs.
        """
        return (
            ((timestamp - self.epoch) << 22)
            | (self.datacenter_id << 17)
            | (self.worker_id << 12)
            | sequence
        )

    def generate(self) -> int:
        """Generate a unique Snowflake ID.

        A contiguous run of sequence numbers is reserved under ``_lock`` once,
        then served from this thread's private batch without locking on every
        call. Reservations are disjoint (the cursor advances under the lock),
        so multi-worker uniqueness is unchanged, and the strict
        ``now < last_timestamp`` clock-rollback guard still raises before any
        state is mutated.
        """
        tl = self._local
        now = self._get_timestamp()

        # Fast path: this thread still owns a reserved run in this exact
        # millisecond. No lock, no shared-state mutation.
        if (
            now >= self.last_timestamp
            and getattr(tl, "ts", None) == now
            and tl.next_seq < tl.end_seq
        ):
            sequence = tl.next_seq
            tl.next_seq += 1
            self.sequence = sequence  # last-issued mirror (observability)
            return self._compose(now, sequence)

        with self._lock:
            # Re-read under the lock: the fast-path clock read can be stale by
            # the time we acquire it. A genuine backwards step still raises.
            now = self._get_timestamp()
            if now < self.last_timestamp:
                raise RuntimeError("Clock moved backwards")

            if self._reserve_cursor is None:
                # Seed from the public field once so pre-generated state (and
                # the 12-bit wrap regression test) is honoured on first use.
                self._reserve_cursor = self.sequence

            if now == self.last_timestamp:
                sequence = self._reserve_cursor + 1
                if sequence > 0xFFF:
                    # Sequence space for this millisecond is exhausted: wait
                    # for the clock to advance, then restart at zero.
                    while now <= self.last_timestamp:
                        now = self._get_timestamp()
                    sequence = 0
            else:
                sequence = 0

            end = min(sequence + self._BATCH_SIZE, 0x1000)
            self._reserve_cursor = end - 1
            self.last_timestamp = now

            tl.ts = now
            tl.next_seq = sequence + 1
            tl.end_seq = end

            self.sequence = sequence
            return self._compose(now, sequence)

    def parse(self, snowflake_id: int | str) -> Dict[str, int]:
        if isinstance(snowflake_id, bool):
            raise ValueError("Invalid snowflake ID")
        if isinstance(snowflake_id, str):
            try:
                snowflake_id = int(snowflake_id)
            except ValueError:
                raise ValueError("Invalid snowflake ID: must be numeric string")
        return {
            "timestamp": (snowflake_id >> 22) + self.epoch,
            "datacenter_id": (snowflake_id >> 17) & 0x1F,
            "worker_id": (snowflake_id >> 12) & 0x1F,
            "sequence": snowflake_id & 0xFFF,
        }
