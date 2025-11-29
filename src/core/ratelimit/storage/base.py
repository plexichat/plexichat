"""
Abstract base class for rate limit storage backends.
"""

from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any


class RateLimitStorage(ABC):
    """Abstract base class for rate limit storage."""

    @abstractmethod
    def get_bucket(self, key: str) -> Optional[Dict[str, Any]]:
        """
        Get bucket state by key.

        Args:
            key: Bucket identifier.

        Returns:
            Bucket state dictionary or None if not found.
        """
        pass

    @abstractmethod
    def set_bucket(self, key: str, state: Dict[str, Any], ttl: Optional[float] = None) -> None:
        """
        Set bucket state.

        Args:
            key: Bucket identifier.
            state: Bucket state dictionary.
            ttl: Time-to-live in seconds (optional).
        """
        pass

    @abstractmethod
    def delete_bucket(self, key: str) -> bool:
        """
        Delete a bucket.

        Args:
            key: Bucket identifier.

        Returns:
            True if deleted, False if not found.
        """
        pass

    @abstractmethod
    def get_keys_by_prefix(self, prefix: str) -> List[str]:
        """
        Get all keys matching a prefix.

        Args:
            prefix: Key prefix to match.

        Returns:
            List of matching keys.
        """
        pass

    @abstractmethod
    def delete_by_prefix(self, prefix: str) -> int:
        """
        Delete all keys matching a prefix.

        Args:
            prefix: Key prefix to match.

        Returns:
            Number of keys deleted.
        """
        pass

    @abstractmethod
    def clear_all(self) -> None:
        """Clear all stored buckets."""
        pass

    @abstractmethod
    def increment(self, key: str, field: str, amount: int = 1) -> int:
        """
        Atomically increment a field in a bucket.

        Args:
            key: Bucket identifier.
            field: Field name to increment.
            amount: Amount to increment by.

        Returns:
            New value after increment.
        """
        pass

    @abstractmethod
    def get_and_set(
        self,
        key: str,
        field: str,
        value: Any,
        default: Any = None
    ) -> Any:
        """
        Atomically get current value and set new value.

        Args:
            key: Bucket identifier.
            field: Field name.
            value: New value to set.
            default: Default if field doesn't exist.

        Returns:
            Previous value.
        """
        pass

    @abstractmethod
    def add_to_list(self, key: str, field: str, value: Any, max_size: int = 1000) -> int:
        """
        Add value to a list field, maintaining max size.

        Args:
            key: Bucket identifier.
            field: List field name.
            value: Value to add.
            max_size: Maximum list size.

        Returns:
            New list size.
        """
        pass

    @abstractmethod
    def trim_list(self, key: str, field: str, min_value: Any) -> int:
        """
        Remove list items less than min_value.

        Args:
            key: Bucket identifier.
            field: List field name.
            min_value: Minimum value to keep.

        Returns:
            Number of items removed.
        """
        pass

    @abstractmethod
    def acquire_lock(self, key: str, timeout: float = 1.0) -> bool:
        """
        Acquire a lock for atomic operations.

        Args:
            key: Lock key.
            timeout: Lock timeout in seconds.

        Returns:
            True if lock acquired, False otherwise.
        """
        pass

    @abstractmethod
    def release_lock(self, key: str) -> None:
        """
        Release a lock.

        Args:
            key: Lock key.
        """
        pass

    def close(self) -> None:
        """Close storage connection (optional override)."""
        pass
