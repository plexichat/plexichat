from src.core.database import check_rate_limit
from .base import SearchManagerBase
from ..exceptions import SearchRateLimitError


class RateLimitMixin(SearchManagerBase):
    def _check_rate_limit(self, user_id: int) -> None:
        rate_limit = self._config.get("rate_limit_per_minute")
        if not rate_limit:
            return

        # The per-user search budget is shared across workers, so its counter
        # must live in the shared rate-limit storage and be updated atomically.
        # The previous generic-cache read-modify-write (cache_get -> +1 ->
        # cache_set) let concurrent callers read the same pre-write value and
        # all pass, and the per-process dict fallback gave every worker its own
        # full budget. ``check_rate_limit`` uses a single atomic INCR+EXPIRE
        # script and fails closed on a backend error, so neither multiplication
        # is possible.
        allowed, _remaining = check_rate_limit(
            f"search:per_user:{int(user_id)}", int(rate_limit), 60
        )
        if not allowed:
            raise SearchRateLimitError(
                "Search rate limit exceeded", retry_after_seconds=60
            )
