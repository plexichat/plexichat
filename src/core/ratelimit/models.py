"""
Rate limit models - Dataclasses for rate limiting.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any
import time


class BucketType(Enum):
    """Types of rate limit buckets."""
    GLOBAL = "global"
    USER = "user"
    ROUTE = "route"
    RESOURCE = "resource"
    WEBHOOK = "webhook"
    CHANNEL_WEBHOOK = "channel_webhook"


class RateLimitAlgorithm(Enum):
    """Rate limiting algorithms."""
    SLIDING_WINDOW = "sliding_window"
    TOKEN_BUCKET = "token_bucket"
    FIXED_WINDOW = "fixed_window"
    LEAKY_BUCKET = "leaky_bucket"


@dataclass
class RateLimitConfig:
    """Configuration for a rate limit bucket."""
    requests: int
    window_seconds: float
    burst: int = 0
    algorithm: RateLimitAlgorithm = RateLimitAlgorithm.SLIDING_WINDOW
    hourly_limit: Optional[int] = None
    daily_limit: Optional[int] = None
    scope: BucketType = BucketType.ROUTE
    include_in_global: bool = True
    retry_after_precision: int = 1

    def __post_init__(self):
        if self.burst == 0:
            self.burst = self.requests

    @property
    def effective_limit(self) -> int:
        """Get effective limit including burst."""
        return self.requests + self.burst if self.burst > self.requests else self.requests

    def with_multiplier(self, multiplier: float) -> "RateLimitConfig":
        """Create a new config with adjusted limits."""
        return RateLimitConfig(
            requests=int(self.requests * multiplier),
            window_seconds=self.window_seconds,
            burst=int(self.burst * multiplier),
            algorithm=self.algorithm,
            hourly_limit=int(self.hourly_limit * multiplier) if self.hourly_limit else None,
            daily_limit=int(self.daily_limit * multiplier) if self.daily_limit else None,
            scope=self.scope,
            include_in_global=self.include_in_global,
            retry_after_precision=self.retry_after_precision,
        )


@dataclass
class RateLimitBucket:
    """State of a rate limit bucket."""
    key: str
    bucket_type: BucketType
    config: RateLimitConfig
    tokens: float = 0.0
    last_update: float = field(default_factory=time.monotonic)
    window_start: float = field(default_factory=time.monotonic)
    request_count: int = 0
    hourly_count: int = 0
    hourly_reset: float = field(default_factory=lambda: time.monotonic() + 3600)
    daily_count: int = 0
    daily_reset: float = field(default_factory=lambda: time.monotonic() + 86400)
    request_timestamps: list = field(default_factory=list)

    def __post_init__(self):
        if self.tokens == 0.0:
            self.tokens = float(self.config.effective_limit)


@dataclass
class RateLimitHeaders:
    """Rate limit response headers."""
    limit: int
    remaining: int
    reset: float
    reset_after: float
    bucket: str
    is_global: bool = False
    retry_after: Optional[float] = None
    scope: str = "user"

    def to_dict(self) -> Dict[str, str]:
        """Convert to HTTP header dictionary."""
        headers = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(max(0, self.remaining)),
            "X-RateLimit-Reset": f"{self.reset:.3f}",
            "X-RateLimit-Reset-After": f"{self.reset_after:.3f}",
            "X-RateLimit-Bucket": self.bucket,
            "X-RateLimit-Scope": self.scope,
        }
        if self.is_global:
            headers["X-RateLimit-Global"] = "true"
        if self.retry_after is not None:
            headers["Retry-After"] = str(int(self.retry_after) + 1)
        return headers


@dataclass
class RateLimitResult:
    """Result of a rate limit check."""
    allowed: bool
    headers: RateLimitHeaders
    bucket_key: str
    bucket_type: BucketType
    remaining: int
    reset_at: float
    retry_after: Optional[float] = None
    is_global: bool = False
    limited_by: Optional[str] = None
    cost: int = 1

    @property
    def response_body(self) -> Dict[str, Any]:
        """Get 429 response body."""
        body = {
            "message": "You are being rate limited.",
            "retry_after": self.retry_after,
            "global": self.is_global,
        }
        if self.limited_by:
            body["scope"] = self.limited_by
        return body


@dataclass
class TokenBucketState:
    """State for token bucket algorithm."""
    tokens: float
    last_refill: float
    capacity: int
    refill_rate: float

    def refill(self, now: float) -> float:
        """Refill tokens based on elapsed time."""
        elapsed = now - self.last_refill
        new_tokens = elapsed * self.refill_rate
        self.tokens = min(self.capacity, self.tokens + new_tokens)
        self.last_refill = now
        return self.tokens

    def consume(self, count: int = 1) -> bool:
        """Try to consume tokens."""
        if self.tokens >= count:
            self.tokens -= count
            return True
        return False


@dataclass
class SlidingWindowState:
    """State for sliding window algorithm."""
    timestamps: list
    window_seconds: float
    limit: int

    def cleanup(self, now: float) -> None:
        """Remove expired timestamps."""
        cutoff = now - self.window_seconds
        self.timestamps = [ts for ts in self.timestamps if ts > cutoff]

    def count(self) -> int:
        """Get current request count in window."""
        return len(self.timestamps)

    def add(self, now: float) -> bool:
        """Add a timestamp if under limit."""
        self.cleanup(now)
        if len(self.timestamps) < self.limit:
            self.timestamps.append(now)
            return True
        return False


@dataclass
class FixedWindowState:
    """State for fixed window algorithm."""
    window_start: float
    window_seconds: float
    count: int
    limit: int

    def check_window(self, now: float) -> None:
        """Reset window if expired."""
        if now >= self.window_start + self.window_seconds:
            self.window_start = now
            self.count = 0

    def increment(self, now: float) -> bool:
        """Increment count if under limit."""
        self.check_window(now)
        if self.count < self.limit:
            self.count += 1
            return True
        return False


@dataclass
class LeakyBucketState:
    """State for leaky bucket algorithm."""
    water_level: float
    last_leak: float
    capacity: int
    leak_rate: float

    def leak(self, now: float) -> float:
        """Leak water based on elapsed time."""
        elapsed = now - self.last_leak
        leaked = elapsed * self.leak_rate
        self.water_level = max(0, self.water_level - leaked)
        self.last_leak = now
        return self.water_level

    def add(self, amount: int = 1) -> bool:
        """Try to add water to bucket."""
        if self.water_level + amount <= self.capacity:
            self.water_level += amount
            return True
        return False
