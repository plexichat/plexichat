"""
Redis client module - Provides Redis connectivity for caching, sessions, and pub/sub.

This module follows the zero-friction pattern established by common-utils.
It requires config and logger to be set up before use.

Features:
    - Connection pooling with automatic reconnection
    - TLS/SSL support for secure connections
    - Key prefixing to avoid collisions
    - Graceful degradation when Redis is unavailable
    - Pub/Sub support for real-time events
    - Health checks and connection monitoring
"""

import sys
import os
import json
import time
from typing import Any, Dict, List, Optional, Union

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")
if common_utils_path not in sys.path:
    sys.path.append(common_utils_path)

import utils.config as config
import utils.logger as logger

# Type aliases
RedisValue = Union[str, bytes, int, float]
JsonSerializable = Union[dict, list, str, int, float, bool, None]


class RedisError(Exception):
    """Base exception for Redis operations."""
    pass


class RedisConnectionError(RedisError):
    """Raised when Redis connection fails."""
    pass


class RedisOperationError(RedisError):
    """Raised when a Redis operation fails."""
    pass


class RedisClient:
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
        self._client = None
        self._pool = None
        self._pubsub = None
        self._connected = False

        # Configuration
        self.host = self.config.get("host", "localhost")
        self.port = self.config.get("port", 6379)
        self.password = self.config.get("password", "") or None
        self.db = self.config.get("db", 0)
        self.ssl = self.config.get("ssl", False)
        self.ssl_cert_reqs = self.config.get("ssl_cert_reqs", "required")
        self.ssl_ca_certs = self.config.get("ssl_ca_certs", "") or None

        # Pool settings
        pool_config = self.config.get("connection_pool", {})
        self.max_connections = pool_config.get("max_connections", 50)
        self.timeout = pool_config.get("timeout", 5)

        # Key prefix
        self.key_prefix = self.config.get("key_prefix", "plexichat:")

        # TTL defaults
        ttl_config = self.config.get("ttl", {})
        self.ttl_session = ttl_config.get("session", 1800)
        self.ttl_presence = ttl_config.get("presence", 300)
        self.ttl_cache = ttl_config.get("cache", 60)

        if self.enabled:
            logger.info(f"Redis client initialized (host={self.host}:{self.port}, ssl={self.ssl})")
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
                "redis is required for Redis support. "
                "Install with: pip install redis"
            )

        try:
            # Build connection pool
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

            # SSL configuration
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

            # Test connection
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
        # Remove any control characters and limit length
        sanitized = "".join(c for c in key if c.isprintable() and c not in "\n\r\t")
        return sanitized[:512]  # Redis key limit is much higher, but we limit for safety

    # ==================== Basic Operations ====================

    def set(self, key: str, value: RedisValue, ttl: Optional[int] = None) -> bool:
        """
        Set a key-value pair.
        
        Args:
            key: The key name.
            value: The value to store.
            ttl: Time-to-live in seconds (optional).
            
        Returns:
            True if successful.
        """
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            if ttl:
                self._client.setex(full_key, ttl, value)
            else:
                self._client.set(full_key, value)
            logger.debug(f"Redis SET: {key}")
            return True
        except Exception as e:
            logger.error(f"Redis SET failed for {key}: {e}")
            raise RedisOperationError(f"SET failed: {e}")

    def get(self, key: str) -> Optional[str]:
        """
        Get a value by key.
        
        Args:
            key: The key name.
            
        Returns:
            The value or None if not found.
        """
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            value = self._client.get(full_key)
            logger.debug(f"Redis GET: {key} -> {'found' if value else 'miss'}")
            return value
        except Exception as e:
            logger.error(f"Redis GET failed for {key}: {e}")
            raise RedisOperationError(f"GET failed: {e}")

    def delete(self, *keys: str) -> int:
        """
        Delete one or more keys.
        
        Args:
            keys: Key names to delete.
            
        Returns:
            Number of keys deleted.
        """
        self._ensure_connected()
        full_keys = [self._prefixed_key(self._sanitize_key(k)) for k in keys]

        try:
            count = self._client.delete(*full_keys)
            logger.debug(f"Redis DELETE: {len(keys)} keys, {count} deleted")
            return count
        except Exception as e:
            logger.error(f"Redis DELETE failed: {e}")
            raise RedisOperationError(f"DELETE failed: {e}")

    def exists(self, key: str) -> bool:
        """Check if a key exists."""
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return bool(self._client.exists(full_key))
        except Exception as e:
            logger.error(f"Redis EXISTS failed for {key}: {e}")
            raise RedisOperationError(f"EXISTS failed: {e}")

    def expire(self, key: str, ttl: int) -> bool:
        """Set expiration on a key."""
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return bool(self._client.expire(full_key, ttl))
        except Exception as e:
            logger.error(f"Redis EXPIRE failed for {key}: {e}")
            raise RedisOperationError(f"EXPIRE failed: {e}")

    def ttl(self, key: str) -> int:
        """Get remaining TTL for a key. Returns -1 if no TTL, -2 if key doesn't exist."""
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return self._client.ttl(full_key)
        except Exception as e:
            logger.error(f"Redis TTL failed for {key}: {e}")
            raise RedisOperationError(f"TTL failed: {e}")

    # ==================== JSON Operations ====================

    def set_json(self, key: str, value: JsonSerializable, ttl: Optional[int] = None) -> bool:
        """
        Store a JSON-serializable value.
        
        Args:
            key: The key name.
            value: Dict, list, or other JSON-serializable value.
            ttl: Time-to-live in seconds (optional).
        """
        try:
            json_str = json.dumps(value, separators=(",", ":"))
            return self.set(key, json_str, ttl)
        except (TypeError, ValueError) as e:
            logger.error(f"JSON serialization failed for {key}: {e}")
            raise RedisOperationError(f"JSON serialization failed: {e}")

    def get_json(self, key: str) -> Optional[JsonSerializable]:
        """
        Get and deserialize a JSON value.
        
        Args:
            key: The key name.
            
        Returns:
            Deserialized value or None if not found.
        """
        value = self.get(key)
        if value is None:
            return None

        try:
            return json.loads(value)
        except (TypeError, ValueError) as e:
            logger.error(f"JSON deserialization failed for {key}: {e}")
            raise RedisOperationError(f"JSON deserialization failed: {e}")

    # ==================== Hash Operations ====================

    def hset(self, name: str, key: str, value: RedisValue) -> int:
        """Set a hash field."""
        self._ensure_connected()
        full_name = self._prefixed_key(self._sanitize_key(name))

        try:
            result = self._client.hset(full_name, key, value)
            logger.debug(f"Redis HSET: {name}.{key}")
            return result
        except Exception as e:
            logger.error(f"Redis HSET failed for {name}.{key}: {e}")
            raise RedisOperationError(f"HSET failed: {e}")

    def hget(self, name: str, key: str) -> Optional[str]:
        """Get a hash field."""
        self._ensure_connected()
        full_name = self._prefixed_key(self._sanitize_key(name))

        try:
            return self._client.hget(full_name, key)
        except Exception as e:
            logger.error(f"Redis HGET failed for {name}.{key}: {e}")
            raise RedisOperationError(f"HGET failed: {e}")

    def hgetall(self, name: str) -> Dict[str, str]:
        """Get all fields in a hash."""
        self._ensure_connected()
        full_name = self._prefixed_key(self._sanitize_key(name))

        try:
            return self._client.hgetall(full_name)
        except Exception as e:
            logger.error(f"Redis HGETALL failed for {name}: {e}")
            raise RedisOperationError(f"HGETALL failed: {e}")

    def hdel(self, name: str, *keys: str) -> int:
        """Delete hash fields."""
        self._ensure_connected()
        full_name = self._prefixed_key(self._sanitize_key(name))

        try:
            return self._client.hdel(full_name, *keys)
        except Exception as e:
            logger.error(f"Redis HDEL failed for {name}: {e}")
            raise RedisOperationError(f"HDEL failed: {e}")

    def hmset(self, name: str, mapping: Dict[str, RedisValue]) -> bool:
        """Set multiple hash fields."""
        self._ensure_connected()
        full_name = self._prefixed_key(self._sanitize_key(name))

        try:
            self._client.hset(full_name, mapping=mapping)
            logger.debug(f"Redis HMSET: {name} ({len(mapping)} fields)")
            return True
        except Exception as e:
            logger.error(f"Redis HMSET failed for {name}: {e}")
            raise RedisOperationError(f"HMSET failed: {e}")

    # ==================== List Operations ====================

    def lpush(self, key: str, *values: RedisValue) -> int:
        """Push values to the left of a list."""
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return self._client.lpush(full_key, *values)
        except Exception as e:
            logger.error(f"Redis LPUSH failed for {key}: {e}")
            raise RedisOperationError(f"LPUSH failed: {e}")

    def rpush(self, key: str, *values: RedisValue) -> int:
        """Push values to the right of a list."""
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return self._client.rpush(full_key, *values)
        except Exception as e:
            logger.error(f"Redis RPUSH failed for {key}: {e}")
            raise RedisOperationError(f"RPUSH failed: {e}")

    def lpop(self, key: str) -> Optional[str]:
        """Pop from the left of a list."""
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return self._client.lpop(full_key)
        except Exception as e:
            logger.error(f"Redis LPOP failed for {key}: {e}")
            raise RedisOperationError(f"LPOP failed: {e}")

    def rpop(self, key: str) -> Optional[str]:
        """Pop from the right of a list."""
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return self._client.rpop(full_key)
        except Exception as e:
            logger.error(f"Redis RPOP failed for {key}: {e}")
            raise RedisOperationError(f"RPOP failed: {e}")

    def lrange(self, key: str, start: int, end: int) -> List[str]:
        """Get a range of elements from a list."""
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return self._client.lrange(full_key, start, end)
        except Exception as e:
            logger.error(f"Redis LRANGE failed for {key}: {e}")
            raise RedisOperationError(f"LRANGE failed: {e}")

    def llen(self, key: str) -> int:
        """Get the length of a list."""
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return self._client.llen(full_key)
        except Exception as e:
            logger.error(f"Redis LLEN failed for {key}: {e}")
            raise RedisOperationError(f"LLEN failed: {e}")

    # ==================== Set Operations ====================

    def sadd(self, key: str, *values: RedisValue) -> int:
        """Add members to a set."""
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return self._client.sadd(full_key, *values)
        except Exception as e:
            logger.error(f"Redis SADD failed for {key}: {e}")
            raise RedisOperationError(f"SADD failed: {e}")

    def srem(self, key: str, *values: RedisValue) -> int:
        """Remove members from a set."""
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return self._client.srem(full_key, *values)
        except Exception as e:
            logger.error(f"Redis SREM failed for {key}: {e}")
            raise RedisOperationError(f"SREM failed: {e}")

    def smembers(self, key: str) -> set:
        """Get all members of a set."""
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return self._client.smembers(full_key)
        except Exception as e:
            logger.error(f"Redis SMEMBERS failed for {key}: {e}")
            raise RedisOperationError(f"SMEMBERS failed: {e}")

    def sismember(self, key: str, value: RedisValue) -> bool:
        """Check if value is a member of a set."""
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return bool(self._client.sismember(full_key, value))
        except Exception as e:
            logger.error(f"Redis SISMEMBER failed for {key}: {e}")
            raise RedisOperationError(f"SISMEMBER failed: {e}")

    # ==================== Counter Operations ====================

    def incr(self, key: str, amount: int = 1) -> int:
        """Increment a counter."""
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return self._client.incrby(full_key, amount)
        except Exception as e:
            logger.error(f"Redis INCR failed for {key}: {e}")
            raise RedisOperationError(f"INCR failed: {e}")

    def decr(self, key: str, amount: int = 1) -> int:
        """Decrement a counter."""
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return self._client.decrby(full_key, amount)
        except Exception as e:
            logger.error(f"Redis DECR failed for {key}: {e}")
            raise RedisOperationError(f"DECR failed: {e}")

    # ==================== Pub/Sub Operations ====================

    def publish(self, channel: str, message: str) -> int:
        """
        Publish a message to a channel.
        
        Args:
            channel: Channel name.
            message: Message to publish.
            
        Returns:
            Number of subscribers that received the message.
        """
        self._ensure_connected()
        full_channel = self._prefixed_key(self._sanitize_key(channel))

        try:
            count = self._client.publish(full_channel, message)
            logger.debug(f"Redis PUBLISH: {channel} -> {count} subscribers")
            return count
        except Exception as e:
            logger.error(f"Redis PUBLISH failed for {channel}: {e}")
            raise RedisOperationError(f"PUBLISH failed: {e}")

    def subscribe(self, *channels: str) -> Any:
        """
        Subscribe to channels.
        
        Args:
            channels: Channel names to subscribe to.
            
        Returns:
            PubSub object for listening to messages.
        """
        self._ensure_connected()
        full_channels = [self._prefixed_key(self._sanitize_key(c)) for c in channels]

        try:
            if not self._pubsub:
                self._pubsub = self._client.pubsub()
            self._pubsub.subscribe(*full_channels)
            logger.debug(f"Redis SUBSCRIBE: {channels}")
            return self._pubsub
        except Exception as e:
            logger.error(f"Redis SUBSCRIBE failed: {e}")
            raise RedisOperationError(f"SUBSCRIBE failed: {e}")

    def unsubscribe(self, *channels: str):
        """Unsubscribe from channels."""
        if not self._pubsub:
            return

        full_channels = [self._prefixed_key(self._sanitize_key(c)) for c in channels]

        try:
            self._pubsub.unsubscribe(*full_channels)
            logger.debug(f"Redis UNSUBSCRIBE: {channels}")
        except Exception as e:
            logger.error(f"Redis UNSUBSCRIBE failed: {e}")
            raise RedisOperationError(f"UNSUBSCRIBE failed: {e}")

    # ==================== Utility Operations ====================

    def ping(self) -> bool:
        """Check if Redis is responsive."""
        if not self._connected or not self._client:
            return False

        try:
            return self._client.ping()
        except Exception:
            return False

    def health_check(self) -> Dict[str, Any]:
        """
        Perform a health check on Redis connection.
        
        Returns:
            Dict with health status information.
        """
        result = {
            "enabled": self.enabled,
            "connected": self._connected,
            "responsive": False,
            "host": self.host,
            "port": self.port,
            "latency_ms": None,
        }

        if not self.enabled or not self._connected:
            return result

        try:
            start = time.time()
            self._client.ping()
            latency = (time.time() - start) * 1000
            result["responsive"] = True
            result["latency_ms"] = round(latency, 2)
        except Exception as e:
            result["error"] = str(e)

        return result

    def flush_prefix(self) -> int:
        """
        Delete all keys with the configured prefix.
        Use with caution!
        
        Returns:
            Number of keys deleted.
        """
        self._ensure_connected()

        try:
            pattern = f"{self.key_prefix}*"
            keys = self._client.keys(pattern)
            if keys:
                return self._client.delete(*keys)
            return 0
        except Exception as e:
            logger.error(f"Redis flush_prefix failed: {e}")
            raise RedisOperationError(f"flush_prefix failed: {e}")

    def keys(self, pattern: str = "*") -> List[str]:
        """
        Get keys matching a pattern.
        
        Args:
            pattern: Glob-style pattern (e.g., "user:*").
            
        Returns:
            List of matching keys (without prefix).
        """
        self._ensure_connected()
        full_pattern = self._prefixed_key(pattern)

        try:
            keys = self._client.keys(full_pattern)
            # Remove prefix from returned keys
            prefix_len = len(self.key_prefix)
            return [k[prefix_len:] if k.startswith(self.key_prefix) else k for k in keys]
        except Exception as e:
            logger.error(f"Redis KEYS failed for {pattern}: {e}")
            raise RedisOperationError(f"KEYS failed: {e}")

    def close(self):
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

    def __enter__(self):
        """Context manager entry - connects to Redis."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - closes connection."""
        self.close()
        return False


# ==================== Module-level convenience functions ====================

_default_client: Optional[RedisClient] = None


def setup() -> Optional[RedisClient]:
    """
    Setup the default Redis client.
    
    Returns:
        RedisClient instance or None if disabled/failed.
    """
    global _default_client
    _default_client = RedisClient()

    if _default_client.enabled:
        try:
            _default_client.connect()
            return _default_client
        except RedisConnectionError as e:
            logger.warning(f"Redis setup failed, continuing without Redis: {e}")
            return None
    return None


def get_client() -> Optional[RedisClient]:
    """Get the default Redis client."""
    return _default_client


def is_available() -> bool:
    """Check if Redis is available and connected."""
    return _default_client is not None and _default_client.ping()
