import importlib
import secrets
import time
from typing import Any, Optional, cast

import utils.logger as logger

from .base import ValkeyClientBase


class LockMixin(ValkeyClientBase):
    def acquire_lock(
        self, key: str, timeout: float = 10.0, lock_timeout: int = 30000
    ) -> Optional[str]:
        glide_sync = cast(Any, importlib.import_module("glide_sync"))
        ExpirySet = glide_sync.ExpirySet
        ExpiryType = glide_sync.ExpiryType
        ConditionalChange = glide_sync.ConditionalChange

        self._ensure_connected()
        client = self._client
        assert client is not None
        full_key = self._prefixed_key(f"lock:{self._sanitize_key(key)}")

        lock_token = secrets.token_hex(16)

        start_time = time.time()
        while time.time() - start_time < timeout:
            result = client.set(
                full_key,
                lock_token,
                conditional_set=ConditionalChange.ONLY_IF_DOES_NOT_EXIST,
                expiry=ExpirySet(ExpiryType.MILLSEC, lock_timeout),
            )
            if result is not None:
                return lock_token
            time.sleep(0.05)
        return None

    def release_lock(self, key: str, token: str) -> bool:
        self._ensure_connected()

        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """

        try:
            # ``eval_lua`` applies the key prefix itself, so pass the SAME
            # sanitized-but-unprefixed name acquire_lock used. The previous
            # code passed the raw (unsanitized) f"lock:{key}" here, so any
            # key needing sanitization was acquired under one name and
            # released under another — the lock was never actually freed.
            result = self.eval_lua(script, [f"lock:{self._sanitize_key(key)}"], [token])
            return bool(result)
        except Exception as e:
            logger.error(f"Failed to release lock for {key}: {e}")
            return False
