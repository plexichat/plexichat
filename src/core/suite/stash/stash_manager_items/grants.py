import time
from typing import Any, Dict, List, Optional

from .._shared import (
    StashNotFoundError,
    StashPermissionError,
    StashValidationError,
)
from ..schema import (
    PERSONAL_ITEM_GRANTABLE_PERMISSIONS,
    STASH_PERMISSIONS,
)
from ..stash_manager_protocol import StashManagerProtocol


class ItemGrantMixin(StashManagerProtocol):
    """Item-level grants."""

    def grant(
        self,
        user_id: int,
        item_id: int,
        subject_id: int,
        permissions: List[str],
        expires_at: Optional[int] = None,
    ) -> Dict[str, Any]:
        row = self._db.fetch_one(
            "SELECT * FROM stash_items WHERE id = ? AND deleted = 0", (int(item_id),)
        )
        if not row:
            raise StashNotFoundError("Stash item not found")
        self._authorize(row, user_id, "share", mutation=True)
        clean = sorted(set(permissions))
        if not clean or any(
            permission not in STASH_PERMISSIONS for permission in clean
        ):
            raise StashValidationError("permissions contains an unknown value")
        # A personal item's owner may only delegate non-privileged permissions.
        # Granting ``share``/``delete``/``manage_versions`` to a third party
        # would let them delete or reshare the owner's item (IDOR / privilege
        # escalation); those stay owned by the item owner.
        if row["scope"] == "personal":
            disallowed = set(clean) & (
                set(STASH_PERMISSIONS) - set(PERSONAL_ITEM_GRANTABLE_PERMISSIONS)
            )
            if disallowed:
                raise StashValidationError(
                    "Personal items cannot delegate privileged permissions: "
                    + ", ".join(sorted(disallowed))
                )
        subject = self._db.fetch_one(
            "SELECT id FROM auth_users WHERE id = ?", (int(subject_id),)
        )
        if not subject:
            raise StashValidationError("subject_id does not identify a user")
        if row["scope"] == "server" and row.get("server_id") is not None:
            if not self._is_member(int(subject_id), int(row["server_id"])):
                raise StashPermissionError(
                    "Grant subject is not a member of the item server"
                )
        grant_id = self._generate_id()
        now = int(time.time() * 1000)
        self._db.upsert(
            "stash_grants",
            [
                "id",
                "item_id",
                "subject_type",
                "subject_id",
                "permissions_json",
                "expires_at",
                "revoked_at",
                "created_by",
                "created_at",
            ],
            (
                grant_id,
                item_id,
                "user",
                str(subject_id),
                self._json(clean, []),
                expires_at,
                None,
                user_id,
                now,
            ),
            ["item_id", "subject_type", "subject_id"],
            ["permissions_json", "expires_at", "revoked_at"],
        )
        # On re-grant the upsert keeps the pre-existing row's id; the freshly
        # generated ``grant_id`` is never persisted in that case. Return the
        # authoritative row so a subsequent ``revoke_grant`` targets the real
        # grant instead of a phantom id (which previously 404'd).
        persisted = self._db.fetch_one(
            "SELECT id, permissions_json, expires_at FROM stash_grants "
            "WHERE item_id = ? AND subject_type = 'user' AND subject_id = ?",
            (int(item_id), str(subject_id)),
        )
        persisted_id = int(persisted["id"]) if persisted else grant_id
        persisted_permissions = (
            self._decode(persisted.get("permissions_json"), clean)
            if persisted
            else clean
        )
        persisted_expiry = persisted.get("expires_at") if persisted else expires_at
        self._audit(
            "share",
            "item",
            int(item_id),
            user_id,
            {"subject_id": int(subject_id), "permissions": clean},
        )
        return {
            "id": str(persisted_id),
            "item_id": str(item_id),
            "subject_type": "user",
            "subject_id": str(subject_id),
            "permissions": persisted_permissions,
            "expires_at": persisted_expiry,
        }

    def revoke_grant(self, user_id: int, grant_id: int) -> None:
        """Revoke a grant after checking that the caller owns the item."""
        grant = self._db.fetch_one(
            "SELECT g.*, i.owner_id FROM stash_grants g "
            "JOIN stash_items i ON i.id = g.item_id WHERE g.id = ? AND i.deleted = 0",
            (int(grant_id),),
        )
        if not grant:
            raise StashNotFoundError("Stash grant not found")
        item = self._db.fetch_one(
            "SELECT * FROM stash_items WHERE id = ?", (int(grant["item_id"]),)
        )
        if not item:
            raise StashNotFoundError("Stash item not found")
        self._authorize(item, user_id, "share", mutation=True)
        self._db.execute(
            "UPDATE stash_grants SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (int(time.time() * 1000), int(grant_id)),
        )
        self._audit(
            "revoke_share",
            "grant",
            int(grant_id),
            user_id,
            {"item_id": int(grant["item_id"])},
        )
