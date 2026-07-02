"""Base class for RedisClient mixins.

Provides shared state (connection, config, connection pools) and
low-level utility methods used by all mixins.
Declares typed attributes and cross-mixin stubs so pyright can
resolve them across all mixin files.
"""

import json
import dataclasses
from enum import Enum
from typing import Any, List, Optional, Union

import utils.config as config
import utils.logger as logger


class EnhancedJSONEncoder(json.JSONEncoder):
    """JSON encoder that handles dataclasses, enums, and sets."""

    def default(self, o):
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return dataclasses.asdict(o)
        if isinstance(o, Enum):
            return o.value
        if isinstance(o, set):
            return list(o)
        if not isinstance(o, type) and hasattr(o, "to_dict"):
            return o.to_dict()
        return super().default(o)


class RedisError(Exception):
    """Base exception for Redis operations."""

    pass


class RedisConnectionError(RedisError):
    """Raised when Redis connection fails."""

    pass


class RedisOperationError(RedisError):
    """Raised when a Redis operation fails."""

    pass


RedisValue = Union[str, bytes, int, float]


class RedisClientBase:
    """
    Redis connection manager with connection pooling and graceful degradation.

    Usage:
        client = RedisClient()
        client.connect()

        # Basic operations
        client.set("key", "value", ttl=300)
        value = client.get("key")

        # JSON operations
        client.set_json("user:1", {"name": "Alice", "age": 30})
        user = client.get_json("user:1")

        # Hash operations
        client.hset("user:1", "name", "Alice")
        name = client.hget("user:1", "name")

        client.close()
    """

    def __init__(self):
        """Initialize the Redis client with configuration."""
        self.config = config.get("redis") or {}
        self.enabled = self.config.get("enabled", False)
        self._client: Any = None
        self._bin_client: Any = None
        self._pool: Any = None
        self._bin_pool: Any = None
        self._pubsub: Any = None
        self._connected = False

        self.host = self.config.get("host", "localhost")
        self.port = self.config.get("port", 6379)
        self.password = self.config.get("password", "") or None
        self.db = self.config.get("db", 0)
        self.ssl = self.config.get("ssl", False)
        self.ssl_cert_reqs = self.config.get("ssl_cert_reqs", "required")
        self.ssl_ca_certs = self.config.get("ssl_ca_certs", "") or None

        pool_config = self.config.get("connection_pool", {})
        self.max_connections = pool_config.get("max_connections", 50)
        self.timeout = pool_config.get("timeout", 5)

        self.key_prefix = self.config.get("key_prefix", "plexichat:")

        ttl_config = self.config.get("ttl", {})
        self.ttl_session = ttl_config.get("session", 1800)
        self.ttl_presence = ttl_config.get("presence", 300)
        self.ttl_cache = ttl_config.get("cache", 60)

        self.worker_id = "unknown"

        if self.enabled:
            logger.info(
                f"Redis client initialized (host={self.host}:{self.port}, ssl={self.ssl})"
            )
        else:
            logger.info("Redis client disabled in configuration")

    def connect(self) -> bool:
        """
        Establish connection to Redis with connection pooling.

        Returns:
            True if connected successfully, False if Redis is disabled or unavailable.

        Raises:
            RedisConnectionError: If connection fails and Redis is required.
        """
        if not self.enabled:
            logger.debug("Redis is disabled, skipping connection")
            return False

        try:
            import redis
        except ImportError:
            logger.error("redis package not installed. Install with: pip install redis")
            raise ImportError(
                "redis is required for Redis support. Install with: pip install redis"
            )

        try:
            pool_kwargs = {
                "host": self.host,
                "port": self.port,
                "db": self.db,
                "password": self.password,
                "max_connections": self.max_connections,
                "socket_timeout": self.timeout,
                "socket_connect_timeout": self.timeout,
                "decode_responses": True,
            }

            if self.ssl:
                import ssl as ssl_module

                pool_kwargs["ssl"] = True
                if self.ssl_cert_reqs == "none":
                    pool_kwargs["ssl_cert_reqs"] = ssl_module.CERT_NONE
                elif self.ssl_cert_reqs == "optional":
                    pool_kwargs["ssl_cert_reqs"] = ssl_module.CERT_OPTIONAL
                else:
                    pool_kwargs["ssl_cert_reqs"] = ssl_module.CERT_REQUIRED
                if self.ssl_ca_certs:
                    pool_kwargs["ssl_ca_certs"] = self.ssl_ca_certs

            self._pool = redis.ConnectionPool(**pool_kwargs)
            self._client = redis.Redis(connection_pool=self._pool)

            bin_pool_kwargs = pool_kwargs.copy()
            bin_pool_kwargs["decode_responses"] = False
            self._bin_pool = redis.ConnectionPool(**bin_pool_kwargs)
            self._bin_client = redis.Redis(connection_pool=self._bin_pool)

            self._client.ping()
            self._connected = True
            logger.info(f"Connected to Redis at {self.host}:{self.port}")
            return True

        except redis.ConnectionError as e:
            logger.error(f"Failed to connect to Redis: {e}")
            self._connected = False
            raise RedisConnectionError(f"Failed to connect to Redis: {e}")
        except Exception as e:
            logger.error(f"Redis connection error: {e}")
            self._connected = False
            raise RedisConnectionError(f"Redis connection error: {e}")

    def set_worker_id(self, worker_id: str) -> None:
        """Set the worker ID for this client instance."""
        self.worker_id = worker_id
        logger.debug(f"Redis client worker ID set to: {worker_id}")

    def _ensure_connected(self):
        """Ensure Redis is connected before operations."""
        if not self.enabled:
            raise RedisOperationError("Redis is disabled in configuration")
        if not self._connected or not self._client:
            raise RedisConnectionError("Redis not connected. Call connect() first.")

    def _prefixed_key(self, key: str) -> str:
        """Add prefix to key for namespace isolation."""
        if key.startswith(self.key_prefix):
            return key
        return f"{self.key_prefix}{key}"

    def _sanitize_key(self, key: str) -> str:
        """Sanitize key to prevent injection attacks."""
        sanitized = "".join(c for c in key if c.isprintable() and c not in "\n\r\t")
        return sanitized[:512]

    def eval_lua(self, script: str, keys: List[str] = [], args: List[Any] = []) -> Any:
        """
        Execute a Lua script.

        Args:
            script: Lua script content.
            keys: List of keys for the script.
            args: List of arguments for the script.

        Returns:
            Script execution result.
        """
        self._ensure_connected()
        client = self._client
        assert client is not None

        prefixed_keys = [self._prefixed_key(k) for k in keys]

        try:
            return client.eval(script, len(keys), *prefixed_keys, *args)
        except Exception as e:
            logger.error(f"Redis EVAL failed: {e}")
            raise RedisOperationError(f"EVAL failed: {e}")

    def close(self) -> None:
        """Close the Redis connection."""
        if self._pubsub:
            try:
                self._pubsub.close()
            except Exception:
                pass
            self._pubsub = None

        if self._pool:
            try:
                self._pool.disconnect()
            except Exception:
                pass
            self._pool = None

        self._client = None
        self._connected = False
        logger.info("Redis connection closed")

    def __enter__(self) -> "RedisClientBase":
        """Context manager entry - connects to Redis."""
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        """Context manager exit - closes connection."""
        self.close()
        return False

    # ==================== Cross-mixin stubs ====================
    # These are overridden by mixins at runtime via the composer class.
    # Declared here so pyright can resolve calls across mixins.

    def set(self, key: str, value: RedisValue, ttl: Optional[int] = None) -> bool:
        """Set a key-value pair. Implemented by BasicMixin."""
        raise NotImplementedError

    def get(self, key: str) -> Optional[str]:
        """Get a value by key. Implemented by BasicMixin."""
        raise NotImplementedError
