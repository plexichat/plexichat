"""Stash manager mixin: collections, share links, media text, diffs (split from manager.py)."""

import difflib
import hashlib
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple

from src.utils import encryption
from .schema import (
    PERSONAL_ITEM_GRANTABLE_PERMISSIONS,
    STASH_PERMISSIONS,
)
from .stash_manager_protocol import StashManagerProtocol
from ._shared import (
    StashNotFoundError,
    StashPermissionError,
    StashValidationError,
)


class StashManagerCollections(StashManagerProtocol):
    def create_collection(
        self,
        user_id: int,
        name: str,
        server_id: Optional[int] = None,
        parent_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        name = str(name or "").strip()
        if not name or len(name) > 200:
            raise StashValidationError(
                "collection name must be between 1 and 200 characters"
            )
        if server_id is not None and not self._is_member(user_id, int(server_id)):
            raise StashPermissionError("You are not a member of this server")
        if parent_id is not None:
            parent = self._db.fetch_one(
                "SELECT id, owner_id, server_id FROM stash_collections WHERE id = ?",
                (int(parent_id),),
            )
            if not parent:
                raise StashValidationError("parent_id does not identify a collection")
            # Nested collections must be owned by the same user and share the
            # same scope: a personal folder cannot nest under a server folder
            # (or vice versa), which would let a server member inherit access
            # to a personal owner's content through a scope-confused parent.
            if int(parent["owner_id"]) != int(user_id):
                raise StashPermissionError(
                    "Parent collection is not owned by this user"
                )
            parent_server = parent.get("server_id")
            if server_id is None:
                if parent_server is not None:
                    raise StashValidationError(
                        "Personal collection cannot nest under a server collection"
                    )
            elif parent_server is None or int(parent_server) != int(server_id):
                raise StashValidationError(
                    "Server collection must nest under the same server"
                )
        collection_id = self._generate_id()
        now = int(time.time() * 1000)
        self._db.execute(
            "INSERT INTO stash_collections (id, owner_id, name, server_id, parent_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (collection_id, user_id, name, server_id, parent_id, now, now),
        )
        self._audit(
            "create",
            "collection",
            collection_id,
            user_id,
            {
                "scope": "server" if server_id is not None else "personal",
                "parent_id": parent_id,
            },
        )
        return {
            "id": str(collection_id),
            "owner_id": str(user_id),
            "name": name,
            "server_id": str(server_id) if server_id is not None else None,
            "parent_id": str(parent_id) if parent_id is not None else None,
            "created_at": now,
            "updated_at": now,
        }

    def list_collections(
        self, user_id: int, server_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        clauses = [
            "(owner_id = ? OR (server_id IS NOT NULL AND server_id IN (SELECT server_id FROM srv_members WHERE user_id = ?)))"
        ]
        params: List[Any] = [user_id, user_id]
        if server_id is not None:
            if not self._is_member(user_id, int(server_id)):
                return []
            clauses.append("server_id = ?")
            params.append(int(server_id))
        rows = self._db.fetch_all(
            f"SELECT * FROM stash_collections WHERE {' AND '.join(clauses)} ORDER BY name",
            tuple(params),
        )
        return [
            {
                "id": str(row["id"]),
                "owner_id": str(row["owner_id"]),
                "name": row["name"],
                "server_id": str(row["server_id"])
                if row.get("server_id") is not None
                else None,
                "parent_id": str(row["parent_id"])
                if row.get("parent_id") is not None
                else None,
                "created_at": int(row["created_at"]),
                "updated_at": int(row["updated_at"]),
            }
            for row in rows
        ]

    # ── collection grants (inherited by contained items) ─────────────────────

    def _collection_row(self, collection_id: int) -> Dict[str, Any]:
        row = self._db.fetch_one(
            "SELECT * FROM stash_collections WHERE id = ?", (int(collection_id),)
        )
        if not row:
            raise StashNotFoundError("Stash collection not found")
        return row

    def _can_grant_collection(self, collection: Dict[str, Any], user_id: int) -> None:
        if int(collection["owner_id"]) == int(user_id):
            return
        server_id = collection.get("server_id")
        if server_id is not None and self._can_administer_server(
            user_id, int(server_id)
        ):
            return
        raise StashPermissionError("Not authorized to manage this collection")

    def grant_collection(
        self,
        user_id: int,
        collection_id: int,
        subject_id: int,
        permissions: List[str],
        expires_at: Optional[int] = None,
    ) -> Dict[str, Any]:
        collection = self._collection_row(collection_id)
        self._can_grant_collection(collection, user_id)
        clean = sorted(set(permissions))
        if not clean or any(
            permission not in STASH_PERMISSIONS for permission in clean
        ):
            raise StashValidationError("permissions contains an unknown value")
        disallowed = set(clean) - set(PERSONAL_ITEM_GRANTABLE_PERMISSIONS)
        if disallowed:
            raise StashValidationError(
                "Collections cannot delegate privileged permissions: "
                + ", ".join(sorted(disallowed))
            )
        subject = self._db.fetch_one(
            "SELECT id FROM auth_users WHERE id = ?", (int(subject_id),)
        )
        if not subject:
            raise StashValidationError("subject_id does not identify a user")
        grant_id = self._generate_id()
        now = int(time.time() * 1000)
        self._db.upsert(
            "stash_collection_grants",
            [
                "id",
                "collection_id",
                "subject_id",
                "permissions_json",
                "expires_at",
                "revoked_at",
                "created_by",
                "created_at",
            ],
            (
                grant_id,
                int(collection_id),
                int(subject_id),
                self._json(clean, []),
                expires_at,
                None,
                user_id,
                now,
            ),
            ["collection_id", "subject_id"],
            ["permissions_json", "expires_at", "revoked_at"],
        )
        persisted = self._db.fetch_one(
            "SELECT id, permissions_json, expires_at FROM stash_collection_grants "
            "WHERE collection_id = ? AND subject_id = ?",
            (int(collection_id), int(subject_id)),
        )
        persisted_id = int(persisted["id"]) if persisted else grant_id
        self._audit(
            "share_collection",
            "collection",
            int(collection_id),
            user_id,
            {"subject_id": int(subject_id), "permissions": clean},
        )
        return {
            "id": str(persisted_id),
            "collection_id": str(collection_id),
            "subject_id": str(subject_id),
            "permissions": self._decode(persisted.get("permissions_json"), clean)
            if persisted
            else clean,
            "expires_at": persisted.get("expires_at") if persisted else expires_at,
        }

    def revoke_collection_grant(self, user_id: int, grant_id: int) -> None:
        grant = self._db.fetch_one(
            "SELECT * FROM stash_collection_grants WHERE id = ?", (int(grant_id),)
        )
        if not grant:
            raise StashNotFoundError("Stash collection grant not found")
        collection = self._collection_row(int(grant["collection_id"]))
        self._can_grant_collection(collection, user_id)
        self._db.execute(
            "UPDATE stash_collection_grants SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (int(time.time() * 1000), int(grant_id)),
        )
        self._audit(
            "revoke_collection_share",
            "collection_grant",
            int(grant_id),
            user_id,
            {"collection_id": int(grant["collection_id"])},
        )

    def list_collection_grants(
        self, user_id: int, collection_id: int
    ) -> List[Dict[str, Any]]:
        collection = self._collection_row(collection_id)
        self._can_grant_collection(collection, user_id)
        rows = self._db.fetch_all(
            "SELECT id, subject_id, permissions_json, expires_at, created_at "
            "FROM stash_collection_grants WHERE collection_id = ? AND revoked_at IS NULL "
            "ORDER BY created_at",
            (int(collection_id),),
        )
        grants: List[Dict[str, Any]] = []
        for row in rows:
            grants.append(
                {
                    "id": str(row["id"]),
                    "collection_id": str(collection_id),
                    "subject_id": str(row["subject_id"]),
                    "permissions": self._decode(row.get("permissions_json"), []),
                    "expires_at": row.get("expires_at"),
                    "created_at": int(row.get("created_at", 0)),
                }
            )
        return grants

    # ── share links (opaque, optionally password-protected, read-only) ───────

    def create_share_link(
        self,
        user_id: int,
        item_id: int,
        *,
        password: Optional[str] = None,
        permissions: Optional[List[str]] = None,
        expires_at: Optional[int] = None,
    ) -> Dict[str, Any]:
        row = self._db.fetch_one(
            "SELECT * FROM stash_items WHERE id = ? AND deleted = 0", (int(item_id),)
        )
        if not row:
            raise StashNotFoundError("Stash item not found")
        self._authorize(row, user_id, "share", mutation=True)
        clean = sorted(set(permissions or ["view_metadata", "preview", "download"]))
        disallowed = set(clean) - {"view_metadata", "preview", "download"}
        if disallowed:
            raise StashValidationError(
                "Share links can only grant read permissions: "
                + ", ".join(sorted(disallowed))
            )
        now = int(time.time() * 1000)
        if expires_at is not None and int(expires_at) <= now:
            raise StashValidationError("Share link expiry must be in the future")
        token = secrets.token_urlsafe(32)
        link_id = self._generate_id()
        self._db.execute(
            "INSERT INTO stash_share_links "
            "(id, item_id, token_hash, password_hash, permissions_json, expires_at, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                link_id,
                int(item_id),
                hashlib.sha256(token.encode("utf-8")).hexdigest(),
                encryption.hash_password(password) if password else None,
                self._json(clean, []),
                expires_at,
                user_id,
                now,
            ),
        )
        self._audit(
            "share_link_created",
            "item",
            int(item_id),
            user_id,
            {"password_protected": bool(password)},
        )
        return {
            "id": str(link_id),
            "item_id": str(item_id),
            "token": token,
            "password_protected": bool(password),
            "permissions": clean,
            "expires_at": expires_at,
            "created_at": now,
        }

    def _resolve_share_link_row(
        self, token: str, password: Optional[str] = None
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Validate a share link's token + password; return ``(link, item)``.

        Raises ``StashPermissionError`` for an invalid/expired/revoked link or a
        wrong password, and ``StashNotFoundError`` when the target item is gone.
        Shared by ``resolve_share_link`` (metadata) and
        ``create_share_download_ticket`` (bytes) so both enforce the same
        checks.
        """
        if not token or len(token) > 256:
            raise StashPermissionError("Invalid or expired share link")
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        link = self._db.fetch_one(
            "SELECT * FROM stash_share_links WHERE token_hash = ?", (token_hash,)
        )
        now = int(time.time() * 1000)
        if not link or link.get("revoked_at") is not None:
            raise StashPermissionError("Invalid or expired share link")
        if link.get("expires_at") is not None and int(link["expires_at"]) <= now:
            raise StashPermissionError("Invalid or expired share link")
        if link.get("password_hash"):
            if not password or not encryption.verify_password(
                password, link["password_hash"]
            ):
                raise StashPermissionError("Invalid or expired share link")
        item = self._db.fetch_one(
            "SELECT * FROM stash_items WHERE id = ? AND deleted = 0",
            (int(link["item_id"]),),
        )
        if not item:
            raise StashNotFoundError("Stash item not found")
        return link, item

    def resolve_share_link(
        self, user_id: int, token: str, password: Optional[str] = None
    ) -> Dict[str, Any]:
        link, item = self._resolve_share_link_row(token, password)
        self._db.execute(
            "UPDATE stash_share_links SET access_count = access_count + 1 WHERE id = ?",
            (int(link["id"]),),
        )
        permissions = self._decode(link.get("permissions_json"), [])
        # A view_metadata-only link must not leak the storage reference: the
        # opaque media_object_id is directly redeemable through the media
        # routes, so it is only exposed when the link grants ``download``.
        # Bytes themselves stay behind the short-lived ticket flow.
        include_storage = "download" in permissions or "*" in permissions
        return {
            "item_id": str(item["id"]),
            "media_object_id": (
                item.get("media_object_id") if include_storage else None
            ),
            "permissions": permissions,
            "expires_at": link.get("expires_at"),
        }

    def create_share_download_ticket(
        self,
        user_id: int,
        token: str,
        password: Optional[str] = None,
        version_id: Optional[int] = None,
        ttl_seconds: int = 300,
    ) -> Dict[str, Any]:
        """Issue a short-lived download ticket to a share-link recipient.

        The share link's token + password are the recipient's authorization; no
        item grant or server membership is required. The link must carry the
        ``download`` permission. The ticket is stamped with the share link id so
        redemption re-validates the link (a revoked/expired link invalidates
        outstanding tickets) instead of the normal item authorization.
        """
        link, item = self._resolve_share_link_row(token, password)
        permissions = self._decode(link.get("permissions_json"), [])
        if "download" not in permissions and "*" not in permissions:
            raise StashPermissionError("Share link does not permit download")
        media_object_id = item.get("media_object_id")
        if version_id is not None:
            version = self._db.fetch_one(
                "SELECT media_object_id FROM stash_versions "
                "WHERE id = ? AND item_id = ?",
                (int(version_id), int(item["id"])),
            )
            if not version:
                raise StashNotFoundError("Stash version not found")
            media_object_id = version.get("media_object_id") or media_object_id
        if not media_object_id:
            raise StashValidationError("Stash item has no downloadable media object")
        try:
            ttl = max(1, min(int(ttl_seconds), 300))
        except (TypeError, ValueError) as exc:
            raise StashValidationError("ttl_seconds must be an integer") from exc

        now = int(time.time() * 1000)
        expires_at = now + ttl * 1000
        ticket = secrets.token_urlsafe(32)
        self._db.execute(
            "INSERT INTO stash_download_tickets "
            "(id, item_id, version_id, subject_id, share_link_id, token_hash, "
            "expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self._generate_id(),
                int(item["id"]),
                int(version_id) if version_id is not None else None,
                int(user_id),
                int(link["id"]),
                hashlib.sha256(ticket.encode("utf-8")).hexdigest(),
                expires_at,
                now,
            ),
        )
        self._audit(
            "download_ticket_created",
            "item",
            int(item["id"]),
            user_id,
            {
                "share_link_id": int(link["id"]),
                "version_id": version_id,
                "expires_at": expires_at,
            },
        )
        return {
            "ticket": ticket,
            "item_id": str(item["id"]),
            "version_id": str(version_id) if version_id is not None else None,
            "media_object_id": str(media_object_id),
            "expires_at": expires_at,
        }

    def revoke_share_link(self, user_id: int, link_id: int) -> None:
        link = self._db.fetch_one(
            "SELECT * FROM stash_share_links WHERE id = ?", (int(link_id),)
        )
        if not link:
            raise StashNotFoundError("Stash share link not found")
        item = self._db.fetch_one(
            "SELECT * FROM stash_items WHERE id = ? AND deleted = 0",
            (int(link["item_id"]),),
        )
        if not item:
            raise StashNotFoundError("Stash item not found")
        self._authorize(item, user_id, "share", mutation=True)
        self._db.execute(
            "UPDATE stash_share_links SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (int(time.time() * 1000), int(link_id)),
        )
        self._audit("share_link_revoked", "item", int(link["item_id"]), user_id)

    def list_share_links(self, user_id: int, item_id: int) -> List[Dict[str, Any]]:
        item = self._db.fetch_one(
            "SELECT * FROM stash_items WHERE id = ? AND deleted = 0", (int(item_id),)
        )
        if not item:
            raise StashNotFoundError("Stash item not found")
        self._authorize(item, user_id, "share", mutation=True)
        rows = self._db.fetch_all(
            "SELECT id, password_hash, permissions_json, expires_at, revoked_at, "
            "access_count, created_at FROM stash_share_links WHERE item_id = ? "
            "ORDER BY created_at",
            (int(item_id),),
        )
        links: List[Dict[str, Any]] = []
        for row in rows:
            links.append(
                {
                    "id": str(row["id"]),
                    "item_id": str(item_id),
                    "password_protected": bool(row.get("password_hash")),
                    "permissions": self._decode(row.get("permissions_json"), []),
                    "expires_at": row.get("expires_at"),
                    "revoked_at": row.get("revoked_at"),
                    "access_count": int(row.get("access_count", 0)),
                    "created_at": int(row.get("created_at", 0)),
                }
            )
        return links

    # ── full-text indexing + version diff ─────────────────────────────────────

    def _media_bytes(self, media_object_id: Optional[str]) -> Optional[bytes]:
        """Read a referenced media object's bytes, if the boundary allows it."""
        if self._media is None or media_object_id is None:
            return None
        value = str(media_object_id)
        file_id: Optional[int] = None
        if value.isdigit():
            file_id = int(value)
        else:
            media_file = self._media.get_file_by_filename(value)
            if media_file is not None:
                file_id = int(media_file.id)
        if file_id is None:
            return None
        try:
            data, _ = self._media.get_file_data(file_id)
            return data if isinstance(data, bytes) else None
        except Exception:
            return None

    @staticmethod
    def _decode_text(data: Optional[bytes]) -> Optional[str]:
        if data is None:
            return None
        try:
            return data.decode("utf-8")
        except (UnicodeDecodeError, AttributeError):
            return None

    def _index_item_text(self, item_id: int, media_object_id: Optional[str]) -> None:
        """Extract and index UTF-8 text from an item's media object."""
        data = self._media_bytes(media_object_id)
        text = self._decode_text(data)
        if text is None:
            return
        # Bound the stored extract so a pathological upload cannot bloat the
        # search index. Binary (undecodable) content is never indexed.
        text = text[:1_000_000]
        self._db.upsert(
            "stash_item_text",
            ["item_id", "content_text", "updated_at"],
            (int(item_id), text, int(time.time() * 1000)),
            ["item_id"],
            ["content_text", "updated_at"],
        )

    def diff_versions(
        self,
        user_id: int,
        item_id: int,
        version_a_id: int,
        version_b_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        item = self._db.fetch_one(
            "SELECT * FROM stash_items WHERE id = ? AND deleted = 0", (int(item_id),)
        )
        if not item:
            raise StashNotFoundError("Stash item not found")
        self._authorize(item, user_id, "download")
        version_a = self._db.fetch_one(
            "SELECT * FROM stash_versions WHERE id = ? AND item_id = ?",
            (int(version_a_id), int(item_id)),
        )
        if not version_a:
            raise StashNotFoundError("Stash version not found")
        if version_b_id is None:
            version_b_id = item.get("current_version_id")
        if version_b_id is None:
            raise StashNotFoundError("Stash version not found")
        version_b = self._db.fetch_one(
            "SELECT * FROM stash_versions WHERE id = ? AND item_id = ?",
            (int(version_b_id), int(item_id)),
        )
        if not version_b:
            raise StashNotFoundError("Stash version not found")

        data_a = self._media_bytes(version_a.get("media_object_id"))
        data_b = self._media_bytes(version_b.get("media_object_id"))
        a_size = len(data_a) if data_a is not None else 0
        b_size = len(data_b) if data_b is not None else 0
        text_a = self._decode_text(data_a)
        text_b = self._decode_text(data_b)
        if (
            text_a is None
            or text_b is None
            or len(text_a) > 1_000_000
            or len(text_b) > 1_000_000
        ):
            # Binary (or oversized) content: report a byte-level equality
            # result plus sizes instead of a line diff.
            a_bytes = data_a if isinstance(data_a, bytes) else b""
            b_bytes = data_b if isinstance(data_b, bytes) else b""
            return {
                "changed": a_bytes != b_bytes,
                "binary": True,
                "diff": [],
                "a_size": a_size,
                "b_size": b_size,
            }
        a_lines = text_a.splitlines(keepends=True)
        b_lines = text_b.splitlines(keepends=True)
        matcher = difflib.SequenceMatcher(None, a_lines, b_lines, autojunk=False)
        op_map = {
            "equal": "equal",
            "replace": "replace",
            "delete": "delete",
            "insert": "insert",
        }
        ops = [
            {
                "op": op_map[tag],
                "a_start": i1,
                "a_end": i2,
                "b_start": j1,
                "b_end": j2,
            }
            for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        ]
        return {
            "changed": text_a != text_b,
            "binary": False,
            "diff": ops,
            "a_size": a_size,
            "b_size": b_size,
        }
