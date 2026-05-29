from ..exceptions import RateLimitError


from .protocol import ApplicationManagerProtocol


class RatelimitMixin(ApplicationManagerProtocol):
    def check_rate_limit(self, application_id: int) -> bool:
        now = self._get_timestamp()
        limits = self._config.get("rate_limits", {})
        requests_per_minute = limits.get("requests_per_minute", 50)

        if application_id not in self._rate_limits:
            self._rate_limits[application_id] = {"count": 0, "reset_at": now + 60000}

        rate_info = self._rate_limits[application_id]

        if now >= rate_info["reset_at"]:
            rate_info["count"] = 0
            rate_info["reset_at"] = now + 60000

        if rate_info["count"] >= requests_per_minute:
            retry_after = (rate_info["reset_at"] - now) // 1000
            raise RateLimitError(
                f"Rate limit exceeded for application {application_id}", retry_after
            )

        rate_info["count"] += 1
        return True
