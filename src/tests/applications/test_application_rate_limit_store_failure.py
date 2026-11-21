"""The application rate limiter must fail closed when the shared store does
not actually apply the token-bucket mutation.

``check_rate_limit`` used to authorise the request purely on the absence of an
``outcome["retry_after"]`` marker. A backend that accepts ``update_bucket``
but silently skips ``mutate_fn`` (swallowed error, unacquirable lock, no-op
fallback) therefore left ``outcome`` empty and the limiter answered ``True``,
turning a store fault into an unlimited-request path. Every other limiter that
spends a shared counter verifies the mutation ran before granting the request.
"""

import pytest

from src.core.applications.exceptions import RateLimitError
from src.core.ratelimit.storage import MemoryStorage


@pytest.mark.applications
def test_application_rate_limit_denies_when_store_skips_mutation(
    app_manager, monkeypatch
):
    """A silently skipped token spend must deny, never report "allowed"."""
    storage = MemoryStorage()

    # Model a store that swallows the write / cannot acquire its lock and
    # returns without calling mutate_fn. No token is spent and no retry_after
    # is recorded, so the old code saw no denial and allowed the request.
    def _skip_mutation(key, mutate_fn, ttl=None):
        return None

    monkeypatch.setattr(storage, "update_bucket", _skip_mutation)
    monkeypatch.setattr(app_manager, "_shared_ratelimit_storage", lambda: storage)

    with pytest.raises(RateLimitError):
        app_manager.check_rate_limit(987654321)


@pytest.mark.applications
def test_application_rate_limit_denies_when_store_raises(app_manager, monkeypatch):
    """An explicit store outage must also deny (no silent legacy fallback)."""
    storage = MemoryStorage()

    def _boom(key, mutate_fn, ttl=None):
        raise RuntimeError("valkey down")

    monkeypatch.setattr(storage, "update_bucket", _boom)
    monkeypatch.setattr(app_manager, "_shared_ratelimit_storage", lambda: storage)

    with pytest.raises(RateLimitError):
        app_manager.check_rate_limit(987654322)
