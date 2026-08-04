from ..exceptions import RateLimitError


from .protocol import ApplicationManagerProtocol


class RatelimitMixin(ApplicationManagerProtocol):
    def check_rate_limit(self, application_id: int) -> bool:
        now = self._get_timestamp()
        limits = self._config.get("rate_limits", {})
        requests_per_minute = limits.get("requests_per_minute", 50)
        burst_limit = limits.get("burst_limit", 10)

        if application_id not in self._rate_limits:
            self._rate_limits[application_id] = {
                "tokens": float(burst_limit),
                "last_refill": now,
                "max_tokens": float(burst_limit),
                "refill_rate": requests_per_minute / 60.0,
            }

        bucket = self._rate_limits[application_id]

        elapsed_ms = now - bucket["last_refill"]
        elapsed_sec = elapsed_ms / 1000.0
        bucket["tokens"] = min(
            bucket["max_tokens"],
            bucket["tokens"] + elapsed_sec * bucket["refill_rate"],
        )
        bucket["last_refill"] = now

        if bucket["tokens"] < 1:
            retry_after = max(1, int((1 - bucket["tokens"]) / bucket["refill_rate"]))
            raise RateLimitError(
                f"Rate limit exceeded for application {application_id}", retry_after
            )

        bucket["tokens"] -= 1
        return True
