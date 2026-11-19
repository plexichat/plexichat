"""
Base service with common functionality.
"""

from typing import Any, Optional
import hashlib
import os
import threading
import time

from src.core.base import SnowflakeID
from src.core.database.collections import CappedDict
import utils.config as config
import utils.logger as logger

# Single source of truth for the ``messaging.encrypt_messages`` default.
# Both the message-send path (which decides actual storage behaviour) and
# the conversation-creation path (which stamps the ``encrypted`` flag on
# msg_conversations rows) MUST read the config through this default so the
# stored flag can never disagree with what send_message actually does.
ENCRYPT_MESSAGES_DEFAULT = True

# Set once at startup to the 10-bit node id claimed from Valkey. Every
# BaseService created afterwards uses it, so all generators in a replica
# share one coordinated identity and no two live replicas collide.
_coordinated_machine_id: Optional[int] = None


def set_machine_id(node_id: int) -> None:
    """Pin the legacy generator's machine id to a coordinated node id."""
    global _coordinated_machine_id
    _coordinated_machine_id = int(node_id) & 0x3FF


def get_encrypt_messages_default() -> bool:
    """Configured encryption-at-rest default (single source of truth)."""
    return bool(
        config.get("messaging", {}).get("encrypt_messages", ENCRYPT_MESSAGES_DEFAULT)
    )


def _default_machine_id() -> int:
    """Derive this replica's 10-bit machine id for the legacy snowflake generator.

    Message/conversation/attachment/pin services still use
    :meth:`BaseService._generate_id`, which packs a 10-bit machine id into the
    ID. The old hardcoded default of ``1`` meant every replica of a
    ``--scale backend=N`` deployment produced the *same* ID for a given
    millisecond and sequence — duplicate primary keys. Derive the id from the
    replica's hostname (unique per container) mixed with the PID, mirroring the
    main ``SnowflakeGenerator``; ``PLEXICHAT_MACHINE_ID`` pins it explicitly.
    """
    raw = os.environ.get("PLEXICHAT_MACHINE_ID")
    if raw is not None:
        try:
            return int(raw) & 0x3FF
        except ValueError:
            pass
    if _coordinated_machine_id is not None:
        return _coordinated_machine_id
    import socket

    try:
        hostname = socket.gethostname()
    except Exception:
        # A constant fallback would collide across replicas (container PIDs
        # are often identical), so use a per-process random value instead.
        hostname = "plexichat-" + os.urandom(16).hex()
    digest = hashlib.sha256(hostname.encode()).digest()
    node = int.from_bytes(digest[:2], "big") & 0x3FF
    node ^= os.getpid() & 0x3FF
    return node


def _get_relationships_module():
    """Return the initialized relationships module, else None.

    Used by DM privacy checks (block enforcement / friends-only DMs).
    Resolves ``src.core.relationships`` directly rather than through the
    ``src.api`` service-locator registry so the check always consults the
    manager bound to the caller's database. A lazy lookup keeps
    ``core.messaging`` decoupled from ``core.relationships`` and preserves
    behaviour for embedders that never set relationships up.
    """
    try:
        from src.core import relationships

        if (
            getattr(relationships, "_setup_complete", False)
            and getattr(relationships, "_manager", None) is not None
        ):
            return relationships
    except Exception as e:
        logger.debug(f"Relationships module unavailable for DM privacy checks: {e}")
    return None


class BaseService:
    """Base class for all services."""

    def __init__(
        self,
        db: Any,
        config_section: str = "messaging",
        cache_max_size: Optional[int] = None,
    ) -> None:
        """
        Initialize service.

        Args:
            db: Database instance
            config_section: Config section name for this service
            cache_max_size: Maximum cache size (None uses config default)
        """
        self._db = db
        self._config = config.get(config_section, {})

        # cache_max_items lives under the "valkey" (or legacy "redis") block.
        valkey_cfg = config.get("valkey") or config.get("redis") or {}
        max_cache = cache_max_size or valkey_cfg.get("cache_max_items", 1000)
        self._cache: CappedDict = CappedDict(max_size=max_cache)
        self._cache_ttl = 60.0  # 60 second cache TTL

        # Snowflake ID generation. The machine id must be unique per replica
        # or scaled workers mint identical IDs in the same millisecond.
        configured_machine_id = config.get("machine_id")
        try:
            self._machine_id = (
                int(configured_machine_id) & 0x3FF
                if configured_machine_id is not None
                else _default_machine_id()
            )
        except (TypeError, ValueError):
            self._machine_id = _default_machine_id()
        self._id_counter = 0
        self._last_timestamp = 0
        # Guard the counter/timestamp state: the sync request threadpool can
        # call _generate_id concurrently even within a single worker.
        self._id_lock = threading.Lock()

    def _get_timestamp(self) -> int:
        """Get current timestamp in milliseconds."""
        return int(time.time() * 1000)

    def _generate_id(self) -> SnowflakeID:
        """Generate a unique Snowflake ID."""
        with self._id_lock:
            timestamp = self._get_timestamp()

            # Mirror the main SnowflakeGenerator: a backwards clock must never
            # be allowed to rewind ``_last_timestamp``. Without this guard a
            # rollback (e.g. an NTP step) resets the sequence and lets a later
            # return to the same millisecond reissue an already-used ID — a
            # duplicate primary key across every replica of a scaled worker.
            # The check runs before any state mutation so a rejected call
            # leaves the counter/timestamp intact.
            if timestamp < self._last_timestamp:
                raise RuntimeError("Clock moved backwards")

            if timestamp == self._last_timestamp:
                self._id_counter = (self._id_counter + 1) & 0xFFF  # 12 bits
                if self._id_counter == 0:
                    # Wait for next millisecond
                    while timestamp <= self._last_timestamp:
                        timestamp = self._get_timestamp()
            else:
                self._id_counter = 0

            self._last_timestamp = timestamp

            # Snowflake format: timestamp (42 bits) | machine_id (10 bits) | sequence (12 bits)
            snowflake = (
                ((timestamp - 1420070400000) << 22)
                | (self._machine_id << 12)
                | self._id_counter
            )
            return snowflake

    def _cache_get(self, key: Any, default: Optional[Any] = None) -> Optional[Any]:
        """Get value from cache (Local memory first, then Valkey)."""
        # 1. Try local memory
        if key in self._cache:
            value, expires = self._cache[key]
            if (self._get_timestamp() / 1000.0) < expires:
                return value
            del self._cache[key]

        # 2. Try Valkey
        from src.core.database import cache_get, valkey_available

        if not valkey_available():
            return default

        cache_key = f"msg_cache:{self.__class__.__name__}:{key}"
        valkey_val = cache_get(cache_key)
        if valkey_val is not None:
            # Store back in local memory for even faster subsequent access
            self._cache_set(key, valkey_val)
            return valkey_val

        return default

    def _cache_set(self, key: Any, value: Any) -> None:
        """Set value in cache (Local memory and Valkey)."""
        self._cache[key] = (value, (self._get_timestamp() / 1000.0) + self._cache_ttl)

        from src.core.database import cache_set, valkey_available

        if valkey_available():
            cache_key = f"msg_cache:{self.__class__.__name__}:{key}"
            cache_set(cache_key, value, ttl=int(self._cache_ttl))

    def _cache_invalidate(self, key: Any) -> None:
        """Invalidate a cache entry (Local memory and Valkey)."""
        self._cache.pop(key, None)

        from src.core.database import cache_delete, valkey_available

        if valkey_available():
            cache_key = f"msg_cache:{self.__class__.__name__}:{key}"
            cache_delete(cache_key)

    def _get_config(self, key: str, default: Any = None) -> Any:
        """Get config value with default."""
        return self._config.get(key, default)
