"""Distributed lock operations mixin."""

import secrets
import time
from typing import Optional

import utils.logger as logger

from .base import RedisClientBase


class LockMixin(RedisClientBase):
    """Mixin providing distributed lock operations."""

    def acquire_lock(
        self, key: str, timeout: float = 10.0, lock_timeout: int = 30000
    ) -> Optional[str]:
        """
        Acquire a distributed lock.

        Args:
            key: Lock key.
            timeout: How long to wait for the lock in seconds.
            lock_timeout: How long the lock is valid for in milliseconds.

        Returns:
            The lock value (token) if acquired, None otherwise.
        """
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_key = self._prefixed_key(f"lock:{self._sanitize_key(key)}")

        lock_token = secrets.token_hex(16)

        start_time = time.time()
        while time.time() - start_time < timeout:
            if client.set(full_key, lock_token, nx=True, px=lock_timeout):
                return lock_token
            time.sleep(0.05)
        return None

    def release_lock(self, key: str, token: str) -> bool:
        """
        Release a distributed lock safely using a Lua script.

        Args:
            key: Lock key.
            token: The token returned by acquire_lock.

        Returns:
            True if the lock was released, False if token mismatch or key missing.
        """
        self._ensure_connected()

        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """

        try:
            self._prefixed_key(f"lock:{self._sanitize_key(key)}")
            result = self.eval_lua(script, [f"lock:{key}"], [token])
            return bool(result)
        except Exception as e:
            logger.error(f"Failed to release lock for {key}: {e}")
            return False
