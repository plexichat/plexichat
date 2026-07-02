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

    def _get_auto_worker_id(self) -> int:
        """Auto-derive worker ID from environment or machine characteristics."""
        # 1. Check environment variable
        env_id = os.environ.get("PLEXICHAT_WORKER_ID")
        if env_id is not None:
            try:
                return int(env_id) % 32
            except ValueError:
                pass

        # 2. Try to derive from process ID and hostname for uniqueness
        import socket

        try:
            hostname = socket.gethostname()
            host_hash = hashlib.sha256(hostname.encode()).digest()
            pid_component = os.getpid() % 8  # 3 bits from PID
            host_component = host_hash[0] % 4  # 2 bits from hostname
            return (host_component << 3) | pid_component
        except Exception:
            return 1

    def _get_auto_datacenter_id(self) -> int:
        """Auto-derive datacenter ID from environment or machine characteristics."""
        # 1. Check environment variable
        env_id = os.environ.get("PLEXICHAT_DATACENTER_ID")
        if env_id is not None:
            try:
                return int(env_id) % 32
            except ValueError:
                pass

        # 2. Try to derive from machine ID or hostname
        import socket

        try:
            hostname = socket.gethostname()
            host_hash = hashlib.sha256(hostname.encode()).digest()
            return host_hash[1] % 32
        except Exception:
            return 1

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

    def parse(self, snowflake_id: int) -> Dict[str, int]:
        return {
            "timestamp": (snowflake_id >> 22) + self.epoch,
            "datacenter_id": (snowflake_id >> 17) & 0x1F,
            "worker_id": (snowflake_id >> 12) & 0x1F,
            "sequence": snowflake_id & 0xFFF,
        }
