"""PlexiStash authorization contract.

The authoritative permission *enforcement* lives in ``StashManager``
(``src/core/suite/stash/manager.py``): every metadata read, preview, download,
mutation, share, and version endpoint resolves access through ``_authorize`` /
``_grant_permissions`` against the item's owner, explicit grants, and server
membership. This module is the canonical home for the permission *vocabulary*
and the pure helpers that describe the same rules, so the contract and the
enforcement cannot silently drift.

Evaluation order enforced by the manager:

1. Authenticate the user and reject suspended/invalid sessions.
2. Resolve the opaque item ID; never accept a filesystem path.
3. Check item/collection ownership and explicit grants.
4. Resolve server-role grants only when the item is server-scoped.
5. Apply inherited grants and explicit revocations.
6. Validate expiring/shared-link state and the requested permission.
7. Apply deletion/version state.
8. Record security-sensitive actions in the Stash audit stream.

Discovery metadata and binary access are separate permissions: seeing a file
name never implies permission to download it, and download URLs are short-lived
and scope-checked at use time, not only when generated.
"""

from typing import Final, FrozenSet, Iterable, Tuple

from .schema import (
    PERSONAL_ITEM_GRANTABLE_PERMISSIONS,
    STASH_PERMISSIONS,
    STASH_SCOPES,
)


STASH_PERMISSION_NAMES: Final[Tuple[str, ...]] = STASH_PERMISSIONS

# Permissions an owner may delegate on a personal item. Privileged mutations
# (``share``, ``delete``, ``manage_versions``) are owner/server-manager only.
PERSONAL_DELEGABLE_PERMISSIONS: Final[FrozenSet[str]] = frozenset(
    PERSONAL_ITEM_GRANTABLE_PERMISSIONS
)

VALID_SCOPES: Final[Tuple[str, ...]] = STASH_SCOPES


def is_valid_permission(permission: str) -> bool:
    """Return whether ``permission`` is a known Stash permission name."""
    return permission in STASH_PERMISSIONS


def validate_permissions(permissions: Iterable[str]) -> Tuple[str, ...]:
    """Normalize a permission list to a sorted, de-duplicated tuple.

    Raises ``ValueError`` on an unknown permission name, mirroring the
    manager's ``StashValidationError`` behavior without importing it (keeps this
    module dependency-light and unit-testable in isolation).
    """
    cleaned = sorted({str(p) for p in permissions})
    unknown = [p for p in cleaned if p not in STASH_PERMISSIONS]
    if unknown:
        raise ValueError("permissions contains unknown value(s): " + ", ".join(unknown))
    return tuple(cleaned)


def delegable_on_personal(permissions: Iterable[str]) -> Tuple[str, ...]:
    """Return the subset of ``permissions`` that may be granted on a personal item."""
    return tuple(
        p
        for p in validate_permissions(permissions)
        if p in PERSONAL_DELEGABLE_PERMISSIONS
    )


def non_delegable_on_personal(permissions: Iterable[str]) -> Tuple[str, ...]:
    """Return the privileged permissions that must never be granted on a personal item."""
    return tuple(
        p
        for p in validate_permissions(permissions)
        if p not in PERSONAL_DELEGABLE_PERMISSIONS
    )


__all__ = [
    "STASH_PERMISSION_NAMES",
    "PERSONAL_DELEGABLE_PERMISSIONS",
    "VALID_SCOPES",
    "is_valid_permission",
    "validate_permissions",
    "delegable_on_personal",
    "non_delegable_on_personal",
]
