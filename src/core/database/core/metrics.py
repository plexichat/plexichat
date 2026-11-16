from contextvars import ContextVar
from typing import Any, Dict, Union

_query_count: ContextVar[int] = ContextVar("_query_count", default=0)
_query_time_ms: ContextVar[float] = ContextVar("_query_time_ms", default=0.0)


class DatabaseMetricsMixin:
    def get_request_metrics(self) -> Dict[str, Union[int, float]]:
        return {
            "query_count": _query_count.get(),
            "query_time_ms": _query_time_ms.get(),
        }

    def reset_request_metrics(self):
        _query_count.set(0)
        _query_time_ms.set(0.0)

    def get_statement_cache_stats(self) -> Dict[str, Any]:
        """Bounded prepared-statement cache instrumentation (hits/evictions).

        Returns an empty dict on partially constructed instances that never
        ran ``_init_once`` (e.g. legacy callers using ``object.__new__``).
        """
        cache = getattr(self, "_statement_cache", None)
        if cache is None:
            return {}
        return cache.stats()
