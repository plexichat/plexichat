"""
Embed validation models.

Typed results for embed validation operations.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class ValidationResult:
    """Result of embed validation."""

    valid: bool
    issues: List[str]
    total_chars: int
    sanitized_data: Optional[Dict[str, Any]] = None
