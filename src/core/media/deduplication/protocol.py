"""
Protocol for deduplication manager mixins providing cross-mixin method signatures.

Used by dedup mixins to declare which methods they expect from sibling
mixins when combined via multiple inheritance.
"""

from typing import Any, Dict, Optional, Tuple


class DeduplicationProtocol:
    _db: Any
    _config: Dict[str, Any]

    def compute_hash(self, file_data: bytes) -> str: ...

    def compute_phash(self, file_data: bytes, content_type: str) -> Optional[str]: ...

    def _find_similar_by_phash(self, phash_value: str) -> Optional[Dict[str, Any]]: ...

    def is_user_blocked(self, user_id: int) -> Tuple[bool, Optional[str]]: ...

    def is_blocked(
        self, hash_value: str, phash_value: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]: ...
