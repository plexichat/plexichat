"""Rules for joining an untrusted relative path onto a trusted base.

Resolved-path comparison, not a string prefix check: ``..`` components, sibling
directories such as ``<base>-evil/``, and symlinked escapes all fail.
"""

from __future__ import annotations

import os

from .errors import HardeningViolation


def safe_join(base: str, *parts: str) -> str:
    """Join ``parts`` onto ``base`` and refuse anything that escapes it."""
    base_resolved = os.path.realpath(base)
    candidate = os.path.realpath(os.path.join(base_resolved, *parts))
    if candidate != base_resolved and not candidate.startswith(base_resolved + os.sep):
        raise HardeningViolation("path escapes its base directory")
    return candidate
