# pyright: reportAttributeAccessIssue=false
"""
Rate-limit helpers mixed into MediaManager.
"""

import logging
from typing import Dict, Any

from src.core import ratelimit
from .exceptions import MediaError

logger = logging.getLogger(__name__)


class _RateLimitMixin:
    """Rate-limit check / update / status methods mixed into MediaManager."""

    def _ensure_ratelimit_setup(self) -> None:
        if ratelimit.is_setup():
            return
        from src.core.ratelimit.storage import MemoryStorage

        ratelimit.setup(storage_backend=MemoryStorage(), enable_global_limit=False)

    # -- upload rate limiting ----------------------------------------------------

    def _check_rate_limit(self, user_id: int, file_size: int) -> None:
        """Check if user is within rate limits.  Raises MediaError if exceeded."""
        rate_config = self._config.get("rate_limit", {})
        if not rate_config.get("enabled", False):
            return
        self._ensure_ratelimit_setup()
        manager = ratelimit.get_manager()

        now_seconds = self._get_timestamp() // 1000
        minute_window = now_seconds - (now_seconds % 60)
        hour_window = now_seconds - (now_seconds % 3600)
        day_window = now_seconds - (now_seconds % 86400)

        minute_key = f"{self._rl_prefix}:uploads:minute:{user_id}:{minute_window}"
        hour_key = f"{self._rl_prefix}:uploads:hour:{user_id}:{hour_window}"
        day_size_key = f"{self._rl_prefix}:size:day:{user_id}:{day_window}"

        uploads_minute = manager.increment_custom_usage(minute_key, "count", 0, ttl=120)
        uploads_hour = manager.increment_custom_usage(hour_key, "count", 0, ttl=7200)
        day_size = manager.increment_custom_usage(
            day_size_key, "total_size", 0, ttl=172800
        )

        max_per_minute = int(rate_config.get("uploads_per_minute", 10))
        max_per_hour = int(rate_config.get("uploads_per_hour", 100))
        max_daily_size = int(
            rate_config.get("max_total_size_per_day", 512 * 1024 * 1024)
        )

        if uploads_minute >= max_per_minute:
            retry_after = 60 - (now_seconds % 60)
            raise MediaError(
                f"Upload rate limit exceeded. Please try again in {int(retry_after)}s"
            )
        if uploads_hour >= max_per_hour:
            retry_after = 3600 - (now_seconds % 3600)
            raise MediaError(
                f"Upload rate limit exceeded. Please try again in {int(retry_after)}s"
            )
        if day_size + file_size > max_daily_size:
            remaining = max(0, max_daily_size - day_size)
            raise MediaError(
                f"Daily upload limit exceeded. Remaining: {remaining // (1024 * 1024)}MB"
            )

    def _update_rate_limit(self, user_id: int, file_size: int) -> None:
        rate_config = self._config.get("rate_limit", {})
        if not rate_config.get("enabled", False):
            return
        self._ensure_ratelimit_setup()
        manager = ratelimit.get_manager()

        now_seconds = self._get_timestamp() // 1000
        minute_window = now_seconds - (now_seconds % 60)
        hour_window = now_seconds - (now_seconds % 3600)
        day_window = now_seconds - (now_seconds % 86400)

        minute_key = f"{self._rl_prefix}:uploads:minute:{user_id}:{minute_window}"
        hour_key = f"{self._rl_prefix}:uploads:hour:{user_id}:{hour_window}"
        day_size_key = f"{self._rl_prefix}:size:day:{user_id}:{day_window}"

        manager.increment_custom_usage(minute_key, "count", 1, ttl=120)
        manager.increment_custom_usage(hour_key, "count", 1, ttl=7200)
        manager.increment_custom_usage(
            day_size_key, "total_size", file_size, ttl=172800
        )

    # -- read helpers ------------------------------------------------------------

    def _get_daily_size(self, user_id: int, day_window: int) -> int:
        self._ensure_ratelimit_setup()
        manager = ratelimit.get_manager()
        size_key = f"{self._rl_prefix}:size:day:{user_id}:{day_window}"
        return manager.increment_custom_usage(size_key, "total_size", 0)

    def _get_rate_limit_count(
        self, user_id: int, window_type: str, window_start: int
    ) -> int:
        self._ensure_ratelimit_setup()
        manager = ratelimit.get_manager()
        if window_type == "minute":
            key = f"{self._rl_prefix}:uploads:minute:{user_id}:{window_start}"
            return manager.increment_custom_usage(key, "count", 0, ttl=120)
        if window_type == "hour":
            key = f"{self._rl_prefix}:uploads:hour:{user_id}:{window_start}"
            return manager.increment_custom_usage(key, "count", 0, ttl=7200)
        return 0

    def _get_rate_limit_size(
        self, user_id: int, window_type: str, window_start: int
    ) -> int:
        self._ensure_ratelimit_setup()
        manager = ratelimit.get_manager()
        if window_type == "day":
            key = f"{self._rl_prefix}:size:day:{user_id}:{window_start}"
            return manager.increment_custom_usage(key, "total_size", 0, ttl=172800)
        return 0

    # -- thumbnail rate limiting -------------------------------------------------

    def _check_thumbnail_rate_limit(self, user_id: int) -> None:
        img_cfg = self._config.get("image_processing", {})
        max_per_minute = int(img_cfg.get("max_thumbnail_requests_per_minute", 60))
        if max_per_minute <= 0:
            return
        self._ensure_ratelimit_setup()
        manager = ratelimit.get_manager()
        now_seconds = self._get_timestamp() // 1000
        minute_window = now_seconds - (now_seconds % 60)
        key = f"{self._rl_prefix}:thumb:minute:{user_id}:{minute_window}"
        count = manager.increment_custom_usage(key, "count", 0, ttl=120)
        if count >= max_per_minute:
            retry_after = 60 - (now_seconds % 60)
            raise MediaError(
                f"Thumbnail rate limit exceeded. Please try again in {int(retry_after)}s"
            )

    def _update_thumbnail_rate_limit(self, user_id: int) -> None:
        img_cfg = self._config.get("image_processing", {})
        max_per_minute = int(img_cfg.get("max_thumbnail_requests_per_minute", 60))
        if max_per_minute <= 0:
            return
        self._ensure_ratelimit_setup()
        manager = ratelimit.get_manager()
        now_seconds = self._get_timestamp() // 1000
        minute_window = now_seconds - (now_seconds % 60)
        key = f"{self._rl_prefix}:thumb:minute:{user_id}:{minute_window}"
        manager.increment_custom_usage(key, "count", 1, ttl=120)

    # -- status endpoint ---------------------------------------------------------

    def get_rate_limit_status(self, user_id: int) -> Dict[str, Any]:
        rate_config = self._config.get("rate_limit", {})
        if not rate_config.get("enabled", True):
            return {"enabled": False}
        self._ensure_ratelimit_setup()
        now_seconds = self._get_timestamp() // 1000

        day_window = now_seconds - (now_seconds % 86400)
        day_size = self._get_rate_limit_size(user_id, "day", day_window)
        minute_window = now_seconds - (now_seconds % 60)
        hour_window = now_seconds - (now_seconds % 3600)
        used_minute = self._get_rate_limit_count(user_id, "minute", minute_window)
        used_hour = self._get_rate_limit_count(user_id, "hour", hour_window)

        max_per_minute = rate_config.get("uploads_per_minute", 10)
        max_per_hour = rate_config.get("uploads_per_hour", 100)
        max_daily_size = rate_config.get("max_total_size_per_day", 512 * 1024 * 1024)

        return {
            "enabled": True,
            "minute": {
                "used": used_minute,
                "limit": max_per_minute,
                "remaining": max(0, max_per_minute - used_minute),
                "resets_in": 60 - (now_seconds % 60),
            },
            "hour": {
                "used": used_hour,
                "limit": max_per_hour,
                "remaining": max(0, max_per_hour - used_hour),
                "resets_in": 3600 - (now_seconds % 3600),
            },
            "day": {
                "used_bytes": day_size,
                "limit_bytes": max_daily_size,
                "remaining_bytes": max(0, max_daily_size - day_size),
                "resets_in": 86400 - (now_seconds % 86400),
            },
        }
