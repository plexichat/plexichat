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
"""

import atexit
import hashlib
import os
import socket
import threading
import uuid

import utils.logger as logger

LEASE_PREFIX = "snowflake:node-lease:"
DEFAULT_LEASE_TTL = 300  # seconds
NODE_ID_SPACE = 1024  # 10 bits: 5-bit datacenter + 5-bit worker


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
        self._thread = None
        # Release on clean interpreter shutdown even if the caller forgets.
        atexit.register(self.release)
        if start_refresh:
            self.start()

    def start(self) -> None:
        """Start the background TTL-refresh thread (idempotent)."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._refresh_loop,
            name="snowflake-node-id-lease",
            daemon=True,
        )
        self._thread.start()

    def _refresh_loop(self) -> None:
        interval = max(1, self.ttl // 3)
        while not self._stop.wait(interval):
            try:
                if self.client.get(self.key) != self.token:
                    logger.error(
                        "Lost Valkey lease for Snowflake node id %d; IDs may "
                        "collide until this worker restarts",
                        self.node_id,
                    )
                    return
                self.client.expire(self.key, self.ttl)
            except Exception as exc:
                logger.warning("Could not refresh Snowflake node-id lease: %s", exc)

    def release(self) -> None:
        """Release the claim so another worker can take the slot."""
        self._stop.set()
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
    and ``delete`` (the project's ``ValkeyClient`` does). Because ``SET NX``
    is atomic, concurrent callers can never win the same slot.

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
