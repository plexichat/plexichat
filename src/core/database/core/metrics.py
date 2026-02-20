from contextvars import ContextVar
from typing import Dict, Union

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
