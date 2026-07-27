import secrets
import time
from typing import Optional

import utils.logger as logger

from .base import ValkeyClientBase
from glide_sync import ExpirySet, ExpiryType, ConditionalChange


class LockMixin(ValkeyClientBase):
    def acquire_lock(
        self, key: str, timeout: float = 10.0, lock_timeout: int = 30000
    ) -> Optional[str]:
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
            self._prefixed_key(f"lock:{self._sanitize_key(key)}")
            result = self.eval_lua(script, [f"lock:{key}"], [token])
            return bool(result)
        except Exception as e:
            logger.error(f"Failed to release lock for {key}: {e}")
            return False
