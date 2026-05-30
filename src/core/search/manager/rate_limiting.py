import time


from src.core.database import cache_get, cache_set, redis_available
from .base import SearchManagerBase
from ..exceptions import SearchRateLimitError


class RateLimitMixin(SearchManagerBase):
    def _check_rate_limit(self, user_id: int) -> None:
        rate_limit = self._config.get("rate_limit_per_minute")
        if not rate_limit:
            return

        if not redis_available():
            now = time.time() * 1000
            window_start = self._search_rate_window_started_ms.get(user_id)
            if window_start is None or now - window_start >= 60_000:
                self._search_rate_window_started_ms[user_id] = now
                self._search_rate_count[user_id] = 0

            self._search_rate_count[user_id] = (
                self._search_rate_count.get(user_id, 0) + 1
            )

            if self._search_rate_count[user_id] > int(rate_limit):
                raise SearchRateLimitError(
                    "Search rate limit exceeded", retry_after_seconds=60
                )
            return

        window_start_key = f"search:rate_limit:{user_id}:window_start"
        count_key = f"search:rate_limit:{user_id}:count"

        now = time.time() * 1000
        window_start = cache_get(window_start_key)

        if window_start is None or (now - float(window_start)) >= 60_000:
            cache_set(window_start_key, now, ttl=60)
            cache_set(count_key, 1, ttl=60)
        else:
            current_count = int(cache_get(count_key) or 0)
            new_count = current_count + 1
            cache_set(count_key, new_count, ttl=60)

            if new_count > int(rate_limit):
                raise SearchRateLimitError(
                    "Search rate limit exceeded", retry_after_seconds=60
                )
