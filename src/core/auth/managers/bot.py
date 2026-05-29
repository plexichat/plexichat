from typing import Optional, Dict, List

from ..exceptions import (
    UserNotFoundError,
    PermissionDeniedError,
    UserExistsError,
    BotLimitExceededError,
    AuthError,
)
from ..models import Bot
from ..permissions import (
    DEFAULT_BOT_PERMISSIONS,
    permissions_to_json,
    permissions_from_json,
    has_permission,
    validate_permissions,
)
from ..tokens import create_bot_token


from .protocol import AuthManagerProtocol


class BotMixin(AuthManagerProtocol):
    def create_bot(
        self,
        owner_id: int,
        username: str,
        display_name: str,
        permissions: Optional[Dict[str, bool]] = None,
    ) -> Bot:
        owner_row = self._db.fetch_one(
            "SELECT permissions FROM auth_users WHERE id = ?", (owner_id,)
        )
        if not owner_row:
            raise UserNotFoundError("User not found")
        owner_perms = permissions_from_json(owner_row["permissions"])
        if not has_permission(owner_perms, "bots.create"):
            raise PermissionDeniedError("Missing required permission: bots.create")

        if self._db.fetch_one(
            "SELECT 1 FROM auth_users WHERE username = ?", (username,)
        ):
            raise UserExistsError("Bot creation failed")
        if self._db.fetch_one(
            "SELECT 1 FROM auth_bots WHERE username = ?", (username,)
        ):
            raise UserExistsError("Bot creation failed")

        bot_config = self._config.get("accounts", {})
        max_bots = bot_config.get("max_bots_per_user", 5)
        current_bots = self.get_user_bots(owner_id)
        if len(current_bots) >= max_bots:
            raise BotLimitExceededError(f"User has reached the bot limit of {max_bots}")

        requested_perms = (
            permissions if permissions is not None else DEFAULT_BOT_PERMISSIONS.copy()
        )
        valid, issues = validate_permissions(requested_perms, is_bot=True)
        if not valid:
            raise AuthError(f"Invalid permissions: {issues}")

        for permission, allowed in requested_perms.items():
            if allowed and not has_permission(owner_perms, permission):
                raise PermissionDeniedError(
                    f"Cannot grant bot permission not held by owner: {permission}"
                )

        if requested_perms.get("bots.create"):
            raise PermissionDeniedError("Bots cannot have the 'bots.create' permission")

        bot_id = self._generate_id()
        token, token_hash = create_bot_token(bot_id)
        perms = requested_perms
        now = self._get_timestamp()

        self._db.begin_transaction()
        try:
            self._db.execute(
                "INSERT INTO auth_users (id, account_type, username, password_hash, permissions, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    bot_id,
                    "bot",
                    username,
                    "!",
                    permissions_to_json(perms),
                    now,
                    now,
                ),
            )

            self._db.execute(
                "INSERT INTO auth_bots (id, owner_id, username, display_name, token_hash, permissions, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    bot_id,
                    owner_id,
                    username,
                    display_name,
                    token_hash,
                    permissions_to_json(perms),
                    now,
                ),
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

        return Bot(
            id=bot_id,
            owner_id=owner_id,
            username=username,
            display_name=display_name,
            permissions=perms.copy(),
            created_at=self._get_timestamp(),
            token=token,
        )

    def get_bot(self, bot_id: int) -> Optional[Bot]:
        row = self._db.fetch_one("SELECT * FROM auth_bots WHERE id = ?", (bot_id,))
        if not row:
            return None
        return Bot(
            id=row["id"],
            owner_id=row["owner_id"],
            username=row["username"],
            display_name=row["display_name"],
            permissions=permissions_from_json(row["permissions"]),
            created_at=row["created_at"],
            disabled=bool(row["disabled"]),
        )

    def get_user_bots(self, owner_id: int) -> List[Bot]:
        rows = self._db.fetch_all(
            "SELECT * FROM auth_bots WHERE owner_id = ?", (owner_id,)
        )
        return [
            Bot(
                id=r["id"],
                owner_id=r["owner_id"],
                username=r["username"],
                display_name=r["display_name"],
                permissions=permissions_from_json(r["permissions"]),
                created_at=r["created_at"],
                disabled=bool(r["disabled"]),
            )
            for r in rows
        ]

    def regenerate_bot_token(self, owner_id: int, bot_id: int) -> str:
        bot_row = self._db.fetch_one(
            "SELECT owner_id FROM auth_bots WHERE id = ?", (bot_id,)
        )
        if not bot_row:
            raise UserNotFoundError("Bot not found")
        if int(bot_row["owner_id"]) != int(owner_id):
            raise PermissionDeniedError("Bot not found or not owned by you")
        token, token_hash = create_bot_token(bot_id)
        cursor = self._db.execute(
            "UPDATE auth_bots SET token_hash = ? WHERE id = ? AND owner_id = ?",
            (token_hash, bot_id, owner_id),
        )
        if cursor.rowcount == 0:
            raise PermissionDeniedError("Bot not found or not owned by you")
        return token

    def update_bot_permissions(
        self, owner_id: int, bot_id: int, permissions: Dict[str, bool]
    ) -> Bot:
        bot_row = self._db.fetch_one(
            "SELECT owner_id FROM auth_bots WHERE id = ?", (bot_id,)
        )
        if not bot_row:
            raise UserNotFoundError("Bot not found")
        if int(bot_row["owner_id"]) != int(owner_id):
            raise PermissionDeniedError("Bot not found or not owned by you")
        valid, issues = validate_permissions(permissions, is_bot=True)
        if not valid:
            raise AuthError(f"Invalid permissions: {issues}")
        cursor = self._db.execute(
            "UPDATE auth_bots SET permissions = ? WHERE id = ? AND owner_id = ?",
            (permissions_to_json(permissions), bot_id, owner_id),
        )
        if cursor.rowcount == 0:
            raise PermissionDeniedError("Bot not found or not owned by you")
        bot = self.get_bot(bot_id)
        if not bot:
            raise PermissionDeniedError("Bot not found or not owned by you")
        return bot

    def disable_bot(self, owner_id: int, bot_id: int) -> bool:
        cursor = self._db.execute(
            "UPDATE auth_bots SET disabled = 1 WHERE id = ? AND owner_id = ?",
            (bot_id, owner_id),
        )
        if cursor.rowcount == 0:
            raise PermissionDeniedError("Bot not found or not owned by you")
        return True

    def enable_bot(self, owner_id: int, bot_id: int) -> bool:
        cursor = self._db.execute(
            "UPDATE auth_bots SET disabled = 0 WHERE id = ? AND owner_id = ?",
            (bot_id, owner_id),
        )
        if cursor.rowcount == 0:
            raise PermissionDeniedError("Bot not found or not owned by you")
        return True

    def delete_bot(self, owner_id: int, bot_id: int) -> bool:
        bot_row = self._db.fetch_one(
            "SELECT owner_id FROM auth_bots WHERE id = ?", (bot_id,)
        )
        if not bot_row:
            raise UserNotFoundError("Bot not found")
        if int(bot_row["owner_id"]) != int(owner_id):
            raise PermissionDeniedError("Bot not found or not owned by you")
        cursor = self._db.execute(
            "DELETE FROM auth_bots WHERE id = ? AND owner_id = ?", (bot_id, owner_id)
        )
        if cursor.rowcount == 0:
            raise PermissionDeniedError("Bot not found or not owned by you")
        return True
