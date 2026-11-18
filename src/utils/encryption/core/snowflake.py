"""
Twitter-style Snowflake ID generator.

Format: [1-bit unused][41-bit timestamp][5-bit datacenter][5-bit worker][12-bit sequence]

Supports auto-derived IDs from machine characteristics for single-machine deployments
and environment variable configuration for distributed deployments.
"""

import hashlib
import os
import threading
import time
from typing import Dict, Optional

import utils.logger as logger


class SnowflakeGenerator:
    """
    Twitter-style Snowflake ID generator.
    Format: [1-bit unused][41-bit timestamp][5-bit datacenter][5-bit worker][12-bit sequence]

    For single-machine deployments, auto-generates IDs from machine characteristics.
    For distributed deployments, set PLEXICHAT_WORKER_ID and PLEXICHAT_DATACENTER_ID.
    """

    def __init__(
        self,
        worker_id: Optional[int] = None,
        datacenter_id: Optional[int] = None,
        epoch_timestamp: Optional[int] = None,
    ):
        # Epoch: 2024-01-01 00:00:00 UTC
        self.epoch = epoch_timestamp or 1704067200000

        # Auto-derive IDs if not provided
        if worker_id is None:
            worker_id = self._get_auto_worker_id()
        if datacenter_id is None:
            datacenter_id = self._get_auto_datacenter_id()

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
            # random value instead — never a shared constant.
            hostname = "plexichat-" + os.urandom(16).hex()
        digest = hashlib.sha256(hostname.encode()).digest()
        node = int.from_bytes(digest[:2], "big") & 0x3FF  # 10 bits
        # Mix the PID so multiple workers on the SAME host still get
        # different identities; mask it to the same 10-bit space.
        node ^= os.getpid() & 0x3FF
        return node

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

    def _get_auto_worker_id(self) -> int:
        """Auto-derive worker ID from environment or machine characteristics."""
        explicit = self._env_node_id("PLEXICHAT_WORKER_ID")
        if explicit is not None:
            return explicit
        return self._auto_node_id() & 0x1F

    def _get_auto_datacenter_id(self) -> int:
        """Auto-derive datacenter ID from environment or machine characteristics."""
        explicit = self._env_node_id("PLEXICHAT_DATACENTER_ID")
        if explicit is not None:
            return explicit
        return (self._auto_node_id() >> 5) & 0x1F

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

    def generate(self) -> int:
        with self._lock:
            timestamp = self._get_timestamp()
            if timestamp < self.last_timestamp:
                raise RuntimeError("Clock moved backwards")

            if timestamp == self.last_timestamp:
                self.sequence = (self.sequence + 1) & 0xFFF
                if self.sequence == 0:
                    while timestamp <= self.last_timestamp:
                        timestamp = self._get_timestamp()
            else:
                self.sequence = 0

            self.last_timestamp = timestamp
            return (
                ((timestamp - self.epoch) << 22)
                | (self.datacenter_id << 17)
                | (self.worker_id << 12)
                | self.sequence
            )

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
