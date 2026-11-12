import hashlib
import secrets
import time
from typing import Any, Dict, Optional, Tuple

from .._shared import (
    StashNotFoundError,
    StashPermissionError,
    StashValidationError,
)
from ..stash_manager_protocol import StashManagerProtocol


class ItemTicketMixin(StashManagerProtocol):
    """Download tickets and their redemption."""

    def create_download_ticket(
        self,
        user_id: int,
        item_id: int,
        version_id: Optional[int] = None,
        ttl_seconds: int = 300,
    ) -> Dict[str, Any]:
        """Create a short-lived opaque ticket for an authorized media object."""
        item = self._db.fetch_one(
            "SELECT * FROM stash_items WHERE id = ? AND deleted = 0", (int(item_id),)
        )
        if not item:
            raise StashNotFoundError("Stash item not found")
        self._authorize(item, user_id, "download")
        media_object_id = item.get("media_object_id")
        if not media_object_id:
            raise StashValidationError("Stash item has no downloadable media object")

        selected_version = None
        if version_id is not None:
            selected_version = self._db.fetch_one(
                "SELECT * FROM stash_versions WHERE id = ? AND item_id = ?",
                (int(version_id), int(item_id)),
            )
            if not selected_version:
                raise StashNotFoundError("Stash version not found")
            if selected_version.get("media_object_id"):
                media_object_id = selected_version["media_object_id"]
        try:
            ttl = max(1, min(int(ttl_seconds), 300))
        except (TypeError, ValueError) as exc:
            raise StashValidationError("ttl_seconds must be an integer") from exc

        now = int(time.time() * 1000)
        expires_at = now + ttl * 1000
        ticket = secrets.token_urlsafe(32)
        self._db.execute(
            "INSERT INTO stash_download_tickets "
            "(id, item_id, version_id, subject_id, token_hash, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                self._generate_id(),
                int(item_id),
                int(version_id) if version_id is not None else None,
                int(user_id),
                hashlib.sha256(ticket.encode("utf-8")).hexdigest(),
                expires_at,
                now,
            ),
        )
        self._audit(
            "download_ticket_created",
            "item",
            int(item_id),
            user_id,
            {"version_id": version_id, "expires_at": expires_at},
        )
        return {
            "ticket": ticket,
            "item_id": str(item_id),
            "version_id": str(version_id) if version_id is not None else None,
            "media_object_id": str(media_object_id),
            "expires_at": expires_at,
        }

    def _ticket_target(
        self, user_id: int, ticket: str
    ) -> Tuple[Dict[str, Any], Optional[Any]]:
        """Resolve a ticket's row + target media object id without consuming it.

        Validates the token, its subject/expiry, the item's liveness, and the
        caller's ``download`` authorization. Returns ``(row, media_object_id)``
        so the caller can sign a URL *before* the ticket is marked redeemed.
        """
        if not ticket or len(ticket) > 256:
            raise StashValidationError("Invalid download ticket")
        token_hash = hashlib.sha256(ticket.encode("utf-8")).hexdigest()
        now = int(time.time() * 1000)
        row = self._db.fetch_one(
            "SELECT * FROM stash_download_tickets WHERE token_hash = ? "
            "AND subject_id = ? AND redeemed_at IS NULL AND expires_at > ?",
            (token_hash, int(user_id), now),
        )
        if not row:
            raise StashPermissionError(
                "Download ticket is invalid, expired, or already redeemed"
            )
        item = self._db.fetch_one(
            "SELECT * FROM stash_items WHERE id = ? AND deleted = 0",
            (int(row["item_id"]),),
        )
        if not item:
            raise StashNotFoundError("Stash item not found")
        share_link_id = row.get("share_link_id")
        if share_link_id is not None:
            # Share-link-issued ticket: the link itself is the authorization.
            # Re-check it at redemption so a revoked/expired link invalidates
            # outstanding tickets even though the recipient holds a valid
            # ticket token.
            link = self._db.fetch_one(
                "SELECT revoked_at, expires_at FROM stash_share_links WHERE id = ?",
                (int(share_link_id),),
            )
            if not link or link.get("revoked_at") is not None:
                raise StashPermissionError("Share link was revoked")
            if link.get("expires_at") is not None and int(link["expires_at"]) <= now:
                raise StashPermissionError("Share link has expired")
        else:
            self._authorize(item, user_id, "download")
        version_id = row.get("version_id")
        media_object_id = item.get("media_object_id")
        if version_id is not None:
            version = self._db.fetch_one(
                "SELECT media_object_id FROM stash_versions WHERE id = ? AND item_id = ?",
                (int(version_id), int(row["item_id"])),
            )
            if version and version.get("media_object_id"):
                media_object_id = version["media_object_id"]
        return row, media_object_id

    def resolve_download_ticket(self, user_id: int, ticket: str) -> Dict[str, Any]:
        """Resolve a ticket's media target *without* consuming it.

        Lets the route sign a media URL first, so a transient media-storage
        failure cannot burn a one-time ticket with nothing returned.
        """
        row, media_object_id = self._ticket_target(user_id, ticket)
        version_id = row.get("version_id")
        return {
            "item_id": str(row["item_id"]),
            "version_id": str(version_id) if version_id is not None else None,
            "media_object_id": str(media_object_id) if media_object_id else None,
            "expires_at": int(row["expires_at"]),
        }

    def redeem_download_ticket(self, user_id: int, ticket: str) -> Dict[str, Any]:
        """Redeem a ticket once, rechecking item authorization at redemption."""
        row, media_object_id = self._ticket_target(user_id, ticket)
        version_id = row.get("version_id")
        now = int(time.time() * 1000)
        cursor = self._db.execute(
            "UPDATE stash_download_tickets SET redeemed_at = ? "
            "WHERE id = ? AND redeemed_at IS NULL AND expires_at > ?",
            (now, int(row["id"]), now),
        )
        try:
            if int(getattr(cursor, "rowcount", 0)) != 1:
                raise StashPermissionError(
                    "Download ticket is invalid, expired, or already redeemed"
                )
        finally:
            cursor.close()
        self._audit(
            "download_ticket_redeemed",
            "item",
            int(row["item_id"]),
            user_id,
            {"version_id": version_id},
        )
        return {
            "item_id": str(row["item_id"]),
            "version_id": str(version_id) if version_id is not None else None,
            "media_object_id": str(media_object_id) if media_object_id else None,
            "expires_at": int(row["expires_at"]),
        }
