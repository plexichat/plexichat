"""
Webhook CRUD operations.
"""

import base64
from typing import Any, Dict, List, Optional

from src.core.base import SnowflakeID
from src.core.webhooks.models import Webhook, WebhookType
from src.core.webhooks.exceptions import (
    ChannelNotFoundError,
    InvalidWebhookTokenError,
    PermissionDeniedError,
    WebhookAccessDeniedError,
    WebhookLimitError,
    WebhookNotFoundError,
)
from src.utils.encryption import encrypt_data, generate_key_pair

from .base import WebhookManagerTrait


# One statement re-checks both webhook quotas at the instant of the insert. The
# old flow ran a ``SELECT COUNT(*)`` for the per-channel and per-server caps and
# only then issued a separate bare ``INSERT``; two concurrent creators could both
# read a below-cap count and both insert, pushing a channel or server past its
# webhook quota (a TOCTOU bypass). The guard subqueries see the table state
# *before* the new row exists, so exactly the callers that still fit under both
# caps get a row, and ``rowcount == 0`` is an authoritative "limit reached"
# signal. SQLite serialises this single statement with its write lock;
# PostgreSQL needs the advisory xact lock in ``create_webhook`` because READ
# COMMITTED would let two statements both see a stale count.
_ATOMIC_WEBHOOK_INSERT_SQL = """
INSERT INTO webhook_webhooks
    (id, channel_id, server_id, creator_id, name, webhook_type,
     avatar_url, token_hash, signing_key_public,
     signing_key_private_encrypted, require_signed_deliveries,
     created_at, updated_at)
SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
WHERE (SELECT COUNT(*) FROM webhook_webhooks WHERE channel_id = ?) < ?
  AND (SELECT COUNT(*) FROM webhook_webhooks WHERE server_id = ?) < ?
"""


class WebhookCRUDMixin(WebhookManagerTrait):
    """Full webhook CRUD operations."""

    def create_webhook(
        self,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        name: str,
        avatar_url: Optional[str] = None,
        require_signed_deliveries: bool = False,
    ) -> Webhook:
        """Create a new webhook for a channel."""
        channel = self._get_channel(channel_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")

        server_id = channel["server_id"]

        if not self._check_manage_webhooks_permission(user_id, server_id, channel_id):
            raise PermissionDeniedError(
                "Missing permission to manage webhooks", "webhooks.manage"
            )

        name = self._validate_name(name)
        avatar_url = self._validate_avatar_url(avatar_url)

        max_per_channel = self._config.get("max_webhooks_per_channel", 10)
        channel_count = self._get_channel_webhook_count(channel_id)
        if channel_count >= max_per_channel:
            raise WebhookLimitError(
                f"Channel has reached maximum of {max_per_channel} webhooks",
                max_per_channel,
                channel_count,
            )

        max_per_server = self._config.get("max_webhooks_per_server", 50)
        server_count = self._get_server_webhook_count(server_id)
        if server_count >= max_per_server:
            raise WebhookLimitError(
                f"Server has reached maximum of {max_per_server} webhooks",
                max_per_server,
                server_count,
            )

        now = self._get_timestamp()
        webhook_id = self._generate_id()
        token_secret = self._generate_token()
        token_hash = self._hash_token(token_secret)
        full_token = self._format_webhook_token(webhook_id, token_secret)

        private_key, public_key = generate_key_pair()
        private_key_encrypted = encrypt_data(
            base64.b64encode(private_key).decode("utf-8"),
            context=f"webhook:{webhook_id}",
        )

        # PostgreSQL READ COMMITTED would let two concurrent guarded INSERTs
        # both see a stale count. Serialise per server with an advisory xact
        # lock. Namespace 130 stays clear of the invite (1/2), emoji (100/101),
        # sticker (102) and soundboard (120) quota locks; masking to 30 bits
        # keeps the second key in signed int4 range.
        is_postgres = getattr(self._db, "type", "") == "postgres"
        if is_postgres:
            self._db.begin_transaction()
            try:
                self._db.execute(
                    "SELECT pg_advisory_xact_lock(?, ?)",
                    (130, int(server_id) & 0x3FFFFFFF),
                )
            except Exception:
                self._db.rollback()
                raise
        try:
            if not is_postgres:
                self._db.begin_transaction()

            cursor = self._db.execute(
                _ATOMIC_WEBHOOK_INSERT_SQL,
                (
                    webhook_id,
                    channel_id,
                    server_id,
                    user_id,
                    name,
                    WebhookType.INCOMING.value,
                    avatar_url,
                    token_hash,
                    public_key,
                    private_key_encrypted,
                    1 if require_signed_deliveries else 0,
                    now,
                    now,
                    channel_id,
                    max_per_channel,
                    server_id,
                    max_per_server,
                ),
            )
            # rowcount 0 means a guard subquery found a quota already full at
            # insert time — a concurrent creator won the race. Roll back and
            # surface the same quota error the advisory check would have raised,
            # re-reading the real counts so the message names the full scope.
            if getattr(cursor, "rowcount", 0) == 0:
                ch_row = self._db.fetch_one(
                    "SELECT COUNT(*) as count FROM webhook_webhooks WHERE channel_id = ?",
                    (channel_id,),
                )
                ch_count = ch_row["count"] if ch_row else 0
                if ch_count >= max_per_channel:
                    raise WebhookLimitError(
                        f"Channel has reached maximum of {max_per_channel} webhooks",
                        max_per_channel,
                        ch_count,
                    )
                sv_row = self._db.fetch_one(
                    "SELECT COUNT(*) as count FROM webhook_webhooks WHERE server_id = ?",
                    (server_id,),
                )
                sv_count = sv_row["count"] if sv_row else 0
                raise WebhookLimitError(
                    f"Server has reached maximum of {max_per_server} webhooks",
                    max_per_server,
                    sv_count,
                )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

        return Webhook(
            id=webhook_id,
            channel_id=channel_id,
            server_id=server_id,
            creator_id=user_id,
            name=name,
            webhook_type=WebhookType.INCOMING,
            avatar_url=avatar_url,
            token=full_token,
            signing_key_public=public_key,
            require_signed_deliveries=bool(require_signed_deliveries),
            created_at=now,
            updated_at=now,
        )

    def get_webhook(
        self, webhook_id: SnowflakeID, user_id: Optional[SnowflakeID] = None
    ) -> Optional[Webhook]:
        """Get a webhook by ID."""
        row = self._db.fetch_one(
            "SELECT * FROM webhook_webhooks WHERE id = ?", (webhook_id,)
        )

        if not row:
            return None

        if user_id is not None:
            if not self._check_manage_webhooks_permission(
                user_id, row["server_id"], row["channel_id"]
            ):
                raise WebhookAccessDeniedError("You do not have access to this webhook")

        return self._row_to_webhook(row, include_token=False)

    def get_webhook_by_token(self, token: str) -> Optional[Webhook]:
        """Get a webhook by its token (for execution)."""
        parsed = self._parse_webhook_token(token)
        if not parsed:
            raise InvalidWebhookTokenError("Invalid webhook token format")

        row = self._db.fetch_one(
            "SELECT * FROM webhook_webhooks WHERE id = ?", (parsed["webhook_id"],)
        )

        if not row:
            raise InvalidWebhookTokenError("Webhook not found")

        if not self._verify_token(parsed["secret"], row["token_hash"]):
            raise InvalidWebhookTokenError("Invalid webhook token")

        return self._row_to_webhook(row, include_token=False)

    def get_channel_webhooks(
        self, user_id: SnowflakeID, channel_id: SnowflakeID
    ) -> List[Webhook]:
        """Get all webhooks for a channel."""
        channel = self._get_channel(channel_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")

        if not self._check_manage_webhooks_permission(
            user_id, channel["server_id"], channel_id
        ):
            raise PermissionDeniedError(
                "Missing permission to view webhooks", "webhooks.manage"
            )

        rows = self._db.fetch_all(
            "SELECT * FROM webhook_webhooks WHERE channel_id = ? ORDER BY created_at",
            (channel_id,),
        )

        return [self._row_to_webhook(row, include_token=False) for row in rows]

    def get_server_webhooks(
        self, user_id: SnowflakeID, server_id: SnowflakeID
    ) -> List[Webhook]:
        """Get all webhooks for a server."""
        server = self._get_server(server_id)
        if not server:
            raise WebhookNotFoundError("Server not found")

        if not self._check_manage_webhooks_permission(user_id, server_id):
            raise PermissionDeniedError(
                "Missing permission to view webhooks", "webhooks.manage"
            )

        rows = self._db.fetch_all(
            "SELECT * FROM webhook_webhooks WHERE server_id = ? ORDER BY created_at",
            (server_id,),
        )

        return [self._row_to_webhook(row, include_token=False) for row in rows]

    def update_webhook(
        self,
        user_id: SnowflakeID,
        webhook_id: SnowflakeID,
        name: Optional[str] = None,
        avatar_url: Optional[str] = None,
        channel_id: Optional[SnowflakeID] = None,
        require_signed_deliveries: Optional[bool] = None,
    ) -> Webhook:
        """Update a webhook."""
        row = self._db.fetch_one(
            "SELECT * FROM webhook_webhooks WHERE id = ?", (webhook_id,)
        )

        if not row:
            raise WebhookNotFoundError("Webhook not found")

        if not self._check_manage_webhooks_permission(
            user_id, row["server_id"], row["channel_id"]
        ):
            raise PermissionDeniedError(
                "Missing permission to manage webhooks", "webhooks.manage"
            )

        sets: List[str] = []
        params: List[Any] = []

        if name is not None:
            name = self._validate_name(name)
            sets.append("name = ?")
            params.append(name)

        if avatar_url is not None:
            if avatar_url == "":
                sets.append("avatar_url = NULL")
            else:
                avatar_url = self._validate_avatar_url(avatar_url)
                sets.append("avatar_url = ?")
                params.append(avatar_url)

        if channel_id is not None and channel_id != row["channel_id"]:
            new_channel = self._get_channel(channel_id)
            if not new_channel:
                raise ChannelNotFoundError("Target channel not found")

            if new_channel["server_id"] != row["server_id"]:
                raise PermissionDeniedError("Cannot move webhook to a different server")

            if not self._check_manage_webhooks_permission(
                user_id, row["server_id"], channel_id
            ):
                raise PermissionDeniedError(
                    "Missing permission to manage webhooks in target channel",
                    "webhooks.manage",
                )

            max_per_channel = self._config.get("max_webhooks_per_channel", 10)
            channel_count = self._get_channel_webhook_count(channel_id)
            if channel_count >= max_per_channel:
                raise WebhookLimitError(
                    f"Target channel has reached maximum of {max_per_channel} webhooks",
                    max_per_channel,
                    channel_count,
                )

            sets.append("channel_id = ?")
            params.append(channel_id)

        if require_signed_deliveries is not None:
            sets.append("require_signed_deliveries = ?")
            params.append(1 if require_signed_deliveries else 0)

        now = self._get_timestamp()
        if not sets:
            self._db.execute(
                "UPDATE webhook_webhooks SET updated_at = ? WHERE id = ?",
                (now, webhook_id),
            )
        else:
            sets.append("updated_at = ?")
            params.append(now)
            params.append(webhook_id)
            placeholders = ", ".join(sets)
            self._db.execute(
                f"UPDATE webhook_webhooks SET {placeholders} WHERE id = ?",
                tuple(params),
            )

        result = self.get_webhook(webhook_id)
        assert result is not None
        return result

    def delete_webhook(self, user_id: SnowflakeID, webhook_id: SnowflakeID) -> bool:
        """Delete a webhook."""
        row = self._db.fetch_one(
            "SELECT * FROM webhook_webhooks WHERE id = ?", (webhook_id,)
        )

        if not row:
            raise WebhookNotFoundError("Webhook not found")

        if not self._check_manage_webhooks_permission(
            user_id, row["server_id"], row["channel_id"]
        ):
            raise PermissionDeniedError(
                "Missing permission to manage webhooks", "webhooks.manage"
            )

        # SECURITY: revoke queued outbound deliveries for this webhook. Retry
        # rows carry the captured endpoint URL and signing headers and are
        # drained by the background worker WITHOUT re-checking that the
        # webhook still exists. Leaving them behind means a deleted (revoked)
        # webhook keeps POSTing event payloads to its old endpoint. Delete
        # every queued row so revocation also takes effect for in-flight work.
        self._db.execute(
            "DELETE FROM webhook_retry_queue WHERE webhook_id = ?", (webhook_id,)
        )

        self._db.execute(
            "DELETE FROM webhook_messages WHERE webhook_id = ?", (webhook_id,)
        )

        self._db.execute("DELETE FROM webhook_webhooks WHERE id = ?", (webhook_id,))

        return True

    def regenerate_token(
        self, user_id: SnowflakeID, webhook_id: SnowflakeID
    ) -> Webhook:
        """Regenerate a webhook's token."""
        row = self._db.fetch_one(
            "SELECT * FROM webhook_webhooks WHERE id = ?", (webhook_id,)
        )

        if not row:
            raise WebhookNotFoundError("Webhook not found")

        if not self._check_manage_webhooks_permission(
            user_id, row["server_id"], row["channel_id"]
        ):
            raise PermissionDeniedError(
                "Missing permission to manage webhooks", "webhooks.manage"
            )

        now = self._get_timestamp()
        token_secret = self._generate_token()
        token_hash = self._hash_token(token_secret)
        full_token = self._format_webhook_token(webhook_id, token_secret)

        self._db.execute(
            "UPDATE webhook_webhooks SET token_hash = ?, updated_at = ? WHERE id = ?",
            (token_hash, now, webhook_id),
        )

        fresh_row = self._db.fetch_one(
            "SELECT * FROM webhook_webhooks WHERE id = ?", (webhook_id,)
        )
        webhook = self._row_to_webhook(fresh_row, include_token=False)
        webhook.token = full_token
        webhook.updated_at = now
        return webhook

    def _row_to_webhook(
        self, row: Dict[str, Any], include_token: bool = False
    ) -> Webhook:
        """Convert a database row to a Webhook object."""
        webhook_id = row["id"]

        return Webhook(
            id=webhook_id,
            channel_id=row["channel_id"],
            server_id=row["server_id"],
            creator_id=row["creator_id"],
            name=row["name"],
            webhook_type=WebhookType(row["webhook_type"]),
            avatar_url=row["avatar_url"],
            token=None,
            signing_key_public=row.get("signing_key_public"),
            signing_key_private_encrypted=row.get("signing_key_private_encrypted"),
            require_signed_deliveries=bool(row.get("require_signed_deliveries") or 0),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
