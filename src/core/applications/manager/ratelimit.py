import utils.logger as logger

from ..exceptions import RateLimitError


from .protocol import ApplicationManagerProtocol


class RatelimitMixin(ApplicationManagerProtocol):
    def check_rate_limit(self, application_id: int) -> bool:
        now = self._get_timestamp()
        limits = self._config.get("rate_limits", {})
        requests_per_minute = limits.get("requests_per_minute", 50)
        burst_limit = limits.get("burst_limit", 10)

        try:
            storage = self._shared_ratelimit_storage()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(
                f"Shared rate-limit storage unavailable, using legacy "
                f"per-process bucket: {exc}"
            )
            return self._legacy_check_rate_limit(
                application_id, requests_per_minute, burst_limit, now
            )

        # Token bucket hosted in the SHARED rate-limit storage (Valkey or a
        # shared database in production), so the limit is enforced across
        # workers/processes instead of per-instance. The per-key lock held
        # by ``update_bucket`` makes the read-modify-write atomic.
        key = f"applications:rate:{int(application_id)}"
        outcome: dict = {}

        def _bucket_check(state: dict) -> None:
            tokens = float(state.get("tokens", float(burst_limit)))
            last_refill = float(state.get("last_refill", now))
            max_tokens = float(burst_limit)
            refill_rate = requests_per_minute / 60.0
            elapsed_ms = max(0, now - last_refill)
            tokens = min(max_tokens, tokens + (elapsed_ms / 1000.0) * refill_rate)
            if tokens < 1.0:
                outcome["retry_after"] = max(1, int((1 - tokens) / refill_rate))
            else:
                tokens -= 1.0
            state["tokens"] = tokens
            state["last_refill"] = now
            state["max_tokens"] = max_tokens
            state["refill_rate"] = refill_rate

        storage.update_bucket(key, _bucket_check, ttl=86400)
        if "retry_after" in outcome:
            raise RateLimitError(
                f"Rate limit exceeded for application {application_id}",
                outcome["retry_after"],
            )
        return True

    def _shared_ratelimit_storage(self):
        """Return the shared, cross-process rate-limit storage backend.

        Raises when the shared manager or its storage backend is not
        available so callers can fall back to the legacy in-process bucket.
        """
        import src.core.ratelimit as ratelimit
        from src.core.ratelimit.storage import MemoryStorage

        if not ratelimit.is_setup():
            ratelimit.setup(storage_backend=MemoryStorage(), enable_global_limit=False)
        manager = ratelimit.get_manager()
        storage = getattr(manager, "_storage", None)
        if storage is None:
            raise RuntimeError("Shared rate-limit manager has no storage backend")
        return storage

    def _legacy_check_rate_limit(
        self,
        application_id: int,
        requests_per_minute: int,
        burst_limit: int,
        now: int,
    ) -> bool:
        """Legacy per-process token bucket (fallback only).

        Kept so isolated callers (e.g. unit tests that never initialise the
        shared rate-limit module) still get the same semantics; production
        always uses the shared storage above.
        """
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
