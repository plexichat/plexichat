import json
from typing import Optional, List, Dict, Any

from ..exceptions import (
    UserNotFoundError,
    InvalidUsernameError,
)
from ..models import User, AccountType
from ..permissions import permissions_to_json, permissions_from_json
from ..passwords import validate_username
from src.core.database import cached, invalidate_pattern


class ProfileMixin:
    def update_user(
        self,
        user_id: int,
        username: Optional[str] = None,
        email: Optional[str] = None,
        permissions: Optional[Dict[str, bool]] = None,
    ) -> User:
        updates = []
        params = []
        if username:
            current_user = self.get_user(user_id)
            if not current_user:
                raise UserNotFoundError("User not found")

            valid, issues = validate_username(username)
            if not valid:
                raise InvalidUsernameError(f"Invalid: {issues}", issues)

            old_username = (
                current_user.username
                if getattr(current_user, "force_username_change", False)
                else None
            )
            blocked, reason = self.blacklist.is_blocked(  # pyright: ignore[reportAttributeAccessIssue]
                username, old_username=old_username
            )
            if blocked:
                raise InvalidUsernameError(
                    f"Username is blocked: {reason}", [reason or "Blocked"]
                )

            updates.append("username = ?")
            params.append(username)

            updates.append("force_username_change = ?")
            params.append(0)

        if email:
            email_index = self.crypto.blind_index(email, "user_email")  # pyright: ignore[reportAttributeAccessIssue]
            email_encrypted = self.crypto.encrypt_data(email, context=str(user_id))  # pyright: ignore[reportAttributeAccessIssue]
            updates.append("email_index = ?, email_encrypted = ?")
            params.extend([email_index, email_encrypted])
        if permissions is not None:
            updates.append("permissions = ?")
            params.append(permissions_to_json(permissions))

        if updates:
            updates.append("updated_at = ?")
            params.append(self._get_timestamp())  # pyright: ignore[reportAttributeAccessIssue]
            params.append(user_id)
            self._db.execute(  # pyright: ignore[reportAttributeAccessIssue]
                f"UPDATE auth_users SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )
            invalidate_pattern("token_verify:*")
            invalidate_pattern("user_data:*")
            from src.core.database import cache_delete

            cache_delete(f"user_profile:{user_id}")

        user = self.get_user(user_id)
        if not user:
            raise UserNotFoundError("User not found")
        return user

    def get_user(self, user_id: int) -> Optional[User]:
        data = self._get_user_data_cached(user_id)
        if not data:
            return None
        user_dict = dict(data)
        if user_dict.get("email_encrypted"):
            try:
                user_dict["email"] = self.crypto.decrypt_data(  # pyright: ignore[reportAttributeAccessIssue]
                    user_dict["email_encrypted"], context=str(user_id)
                )
            except Exception:
                user_dict["email"] = None
        if user_dict.get("date_of_birth"):
            try:
                user_dict["dob_decrypted"] = self.crypto.decrypt_data(  # pyright: ignore[reportAttributeAccessIssue]
                    user_dict["date_of_birth"], context=str(user_id)
                )
            except Exception:
                user_dict["dob_decrypted"] = None
        return self._dict_to_user(user_dict)

    @cached(ttl=60, prefix="user_data")
    def _get_user_data_cached(self, user_id: int) -> Optional[Dict[str, Any]]:
        query = """
            SELECT u.*, f.badges
            FROM auth_users u
            LEFT JOIN user_features f ON u.id = f.user_id
            WHERE u.id = ?
        """
        row = self._db.fetch_one(query, (user_id,))  # pyright: ignore[reportAttributeAccessIssue]
        if not row:
            return None

        user_dict = dict(row)

        if user_dict.get("badges"):
            try:
                user_dict["badges_list"] = (
                    json.loads(user_dict["badges"])
                    if isinstance(user_dict["badges"], str)
                    else user_dict["badges"]
                )
            except Exception:
                user_dict["badges_list"] = []
        else:
            user_dict["badges_list"] = []

        return user_dict

    def _dict_to_user(self, row: Dict[str, Any]) -> User:
        return User(
            id=row["id"],
            account_type=AccountType(row["account_type"]),
            username=row["username"],
            email=row.get("email"),
            permissions=permissions_from_json(row["permissions"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            email_verified=bool(row.get("email_verified", 0)),
            account_locked=bool(row.get("account_locked", 0)),
            failed_login_attempts=row.get("failed_login_attempts", 0),
            locked_until=row.get("locked_until"),
            last_login_at=row.get("last_login_at"),
            totp_enabled=bool(row.get("totp_enabled", 0)),
            age_verified=bool(row.get("age_verified", 0)),
            date_of_birth=row.get("dob_decrypted") or row.get("date_of_birth"),
            force_username_change=bool(row.get("force_username_change", 0)),
            badges=row.get("badges_list", []),
            public_key=row.get("public_key"),
            deletion_status=row.get("deletion_status", "active"),
            deletion_at=row.get("deletion_at"),
        )

    @cached(ttl=300, prefix="user_by_username")
    def get_user_by_username(self, username: str) -> Optional[User]:
        row = self._db.fetch_one(  # pyright: ignore[reportAttributeAccessIssue]
            "SELECT id FROM auth_users WHERE username = ?", (username,)
        )
        if not row:
            return None
        return self.get_user(row["id"])

    def get_user_profiles_bulk(self, user_ids: List[int]) -> Dict[str, Any]:
        if not user_ids:
            return {}

        result = {}
        missing_ids = []

        from src.core.database import cache_get, cache_set, redis_available

        for uid in user_ids:
            cache_key = f"user_profile:{uid}"
            cached_profile = cache_get(cache_key) if redis_available() else None

            if cached_profile:
                if isinstance(cached_profile, str):
                    try:
                        cached_profile = json.loads(cached_profile)
                    except Exception:
                        pass
                if isinstance(cached_profile, dict) and "created_at" in cached_profile:
                    result[str(uid)] = cached_profile
                else:
                    missing_ids.append(uid)
            else:
                missing_ids.append(uid)

        if missing_ids:
            placeholders = ",".join("?" for _ in missing_ids)
            query = f"""
                SELECT u.id, u.username, u.created_at, u.permissions, u.account_type, f.badges
                FROM auth_users u
                LEFT JOIN user_features f ON u.id = f.user_id
                WHERE u.id IN ({placeholders})
            """
            rows = self._db.fetch_all(query, tuple(missing_ids))  # pyright: ignore[reportAttributeAccessIssue]

            for row in rows:
                user_id = row["id"]
                badges = []
                if row.get("badges"):
                    try:
                        badges = (
                            json.loads(row["badges"])
                            if isinstance(row["badges"], str)
                            else row["badges"]
                        )
                    except Exception:
                        badges = []

                profile = {
                    "id": user_id,
                    "username": row["username"],
                    "created_at": row["created_at"],
                    "permissions": self._json_loads(row["permissions"])  # pyright: ignore[reportAttributeAccessIssue]
                    if isinstance(row["permissions"], str)
                    else row["permissions"],
                    "account_type": row["account_type"],
                    "avatar_url": f"/api/v1/avatars/users/{user_id}",
                    "badges": badges,
                }
                result[str(user_id)] = profile

                if redis_available():
                    cache_set(f"user_profile:{user_id}", profile, ttl=300)

        return result

    def get_users_bulk(self, user_ids: List[int]) -> Dict[int, User]:
        if not user_ids:
            return {}

        placeholders = ",".join("?" for _ in user_ids)
        query = f"""
            SELECT
                u.*,
                f.rate_limit_tier AS tier,
                f.badges,
                CASE WHEN u.account_type = 'bot' THEN 1 ELSE 0 END AS is_bot
            FROM auth_users u
            LEFT JOIN user_features f ON u.id = f.user_id
            WHERE u.id IN ({placeholders})
        """
        rows = self._db.fetch_all(query, tuple(user_ids))  # pyright: ignore[reportAttributeAccessIssue]

        result = {}
        for row in rows:
            user_id = row["id"]
            user_dict = dict(row)

            if user_dict.get("badges"):
                try:
                    user_dict["badges_list"] = (
                        json.loads(user_dict["badges"])
                        if isinstance(user_dict["badges"], str)
                        else user_dict["badges"]
                    )
                except Exception:
                    user_dict["badges_list"] = []
            else:
                user_dict["badges_list"] = []

            if user_dict.get("email_encrypted"):
                try:
                    user_dict["email"] = self.crypto.decrypt_data(  # pyright: ignore[reportAttributeAccessIssue]
                        user_dict["email_encrypted"], context=str(user_id)
                    )
                except Exception:
                    user_dict["email"] = None

            if user_dict.get("date_of_birth"):
                try:
                    user_dict["dob_decrypted"] = self.crypto.decrypt_data(  # pyright: ignore[reportAttributeAccessIssue]
                        user_dict["date_of_birth"], context=str(user_id)
                    )
                except Exception:
                    user_dict["dob_decrypted"] = None

            result[user_id] = self._dict_to_user(user_dict)

        return result

    def grant_permission(self, user_id: int, permission: str) -> bool:
        user = self.get_user(user_id)
        if not user:
            return False
        perms = user.permissions.copy()
        perms[permission] = True
        self.update_user(user_id, permissions=perms)
        return True
