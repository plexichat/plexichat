import copy
import json
from typing import Optional, List, Dict, Any

import utils.logger as logger

from ..exceptions import (
    UserNotFoundError,
    InvalidUsernameError,
    InvalidEmailError,
    InvalidPermissionError,
)
from ..models import User, AccountType
from ..permissions import permissions_to_json, permissions_from_json
from ..passwords import validate_username, validate_email
from src.core.database import cached, invalidate_pattern


from .protocol import AuthManagerProtocol
from .registration import MAX_EMAIL_LENGTH


# ``get_user_profiles_bulk`` is the shared username/display-name resolver for
# every listing (message pages, member lists, search results, DMs, ban/grant
# lists, ...).  Before this tier it re-read the shared cache once per user on
# every page -- and skipped the cache entirely when Valkey was unavailable,
# re-querying ``auth_users`` for every listing request.  A short-TTL,
# process-local L1 keeps warm ids off both paths.  The entry lives under the
# SAME ``user_profile:{uid}`` key as the shared cache, so every existing
# ``cache_delete`` / ``invalidate_pattern("user_profile:*")`` mutation hook
# (profile edit, deletion, feature/badge change, admin edit) clears it too.
# ``src.core.database.cache.base`` already bounds that tier by entry count and
# bytes; the short TTL additionally bounds cross-worker staleness.
PROFILE_BULK_L1_TTL_SECONDS = 10

# Observability counters for the L1 tier (also used by the query-count
# regression tests).  Never part of the response contract.
# ``valkey_gets`` counts shared-cache *keys* requested (to show how much the L1
# tier saves); ``valkey_bulk_calls`` counts the round trips used to fetch them.
# On a cold L1 the old code made one GET per key; now it makes a single MGET.
_profile_bulk_l1_stats = {
    "hits": 0,
    "misses": 0,
    "valkey_gets": 0,
    "valkey_bulk_calls": 0,
}


def get_profile_bulk_l1_stats() -> Dict[str, int]:
    """Return a copy of the process-local profile L1 counters."""
    return dict(_profile_bulk_l1_stats)


def reset_profile_bulk_l1_stats() -> None:
    """Zero the L1 counters (test/telemetry helper)."""
    for key in _profile_bulk_l1_stats:
        _profile_bulk_l1_stats[key] = 0


class ProfileMixin(AuthManagerProtocol):
    def update_user(
        self,
        user_id: int,
        username: Optional[str] = None,
        email: Optional[str] = None,
        permissions: Optional[Dict[str, bool]] = None,
        allow_wildcards: bool = False,
    ) -> User:
        """Update user profile fields.

        Args:
            user_id: ID of the user to update.
            username: New username (optional).
            email: New email address (optional).
            permissions: Replacement permission map (optional).
            allow_wildcards: Explicit opt-in for privileged callers
                (admin provisioning, self-test) to persist wildcard keys
                (``"*"`` and ``"<category>.*"``). Defaults to ``False`` so
                user-controlled paths can never escalate via wildcards (M5).
                Even when enabled, wildcards are validated against known
                permission categories and bot restrictions still apply.
        """
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
            blocked, reason = self.blacklist.is_blocked(
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

        email_changed = False
        if email:
            # M4: the registration path validates email format; profile updates
            # previously skipped it entirely and relied on the DB constraint
            # for uniqueness, so a malformed address was accepted/stored.
            # Enforce the same structural ceiling as registration so a direct
            # manager call cannot smuggle an unbounded value past the API's
            # EmailStr bound into blind indexes / encrypted columns.
            if not isinstance(email, str) or len(email) > MAX_EMAIL_LENGTH:
                raise InvalidEmailError("Invalid email")
            if not validate_email(email):
                raise InvalidEmailError("Invalid email")
            current_for_email = self.get_user(user_id)
            if not current_for_email:
                raise UserNotFoundError("User not found")
            # Only an actual address change demotes verification and triggers
            # a new verification email (resubmitting the same address is a
            # no-op for the verified flag).
            email_changed = email != current_for_email.email
            if email_changed:
                email_index = self.crypto.blind_index(email, "user_email")
                email_encrypted = self.crypto.encrypt_data(email, context=str(user_id))
                # M2 FIX: a changed address is unverified until the user
                # completes the email-verification flow for the new value.
                # Forcing the flag down here (manager level) means no route
                # can accidentally leave a hijacked/stale session holding a
                # verified new address.
                updates.append(
                    "email_index = ?, email_encrypted = ?, email_verified = ?"
                )
                params.extend([email_index, email_encrypted, 0])

                # Recovery-token replay fix: an email token row carries only a
                # user id, not the address it was minted for. Without this, a
                # token mailed to an address the user used to control can be
                # replayed after the switch. For verification that would flip
                # ``email_verified`` on for the NEW (unconfirmed) address; for
                # password reset it is worse — an attacker who captured a link
                # from the old inbox keeps a working account-takeover credential
                # even after the victim moved to a new address to reclaim the
                # account. Consume EVERY outstanding address-delivered token at
                # the instant the address changes so only freshly issued links
                # (sent to the new address) remain usable. That a token has no
                # address column is exactly why it must be retired here, not a
                # reason to leave it live.
                self._db.execute(
                    "UPDATE auth_email_tokens SET used = 1 "
                    "WHERE user_id = ? AND token_type IN ('verify_email', 'reset_password') "
                    "AND used = 0",
                    (user_id,),
                )
                # A retired reset token must not keep answering from a warm
                # verification cache. Fail closed: the address change is a
                # credential transition, so drop cached verdicts at once.
                invalidate_pattern("token_verify:*")

                # An address change is a credential transition: the email is
                # the account's recovery identifier and the destination for
                # password-reset links. Every session created while the old
                # address was on file must die with it, otherwise a stale or
                # hijacked session keeps full authenticated access after the
                # account has moved to a new address. Mirror the password
                # change/reset revocation so the switch actually re-secures
                # the account.
                self._db.execute(
                    "UPDATE auth_sessions SET revoked = 1 WHERE user_id = ?",
                    (user_id,),
                )
                # A device marked trusted skips the 2FA challenge at login with
                # only the password, i.e. it is a persistent second-factor
                # bypass. The address change above retired every session but
                # that is not enough: a stale trusted device (lost, stolen, or
                # in an attacker's hands) would keep skipping 2FA with the
                # account's new recovery identifier. Mirror password change /
                # reset and 2FA enable: retire remembered devices as part of
                # the same credential transition.
                self._invalidate_trusted_devices(user_id)
        if permissions is not None:
            from ..permissions import PERMISSIONS, BOT_RESTRICTED_PERMISSIONS

            valid_prefixes = {p.split(".")[0] for p in PERMISSIONS}
            current_user_for_perms = self.get_user(user_id)
            is_bot = bool(
                current_user_for_perms is not None
                and getattr(current_user_for_perms, "account_type", None)
                and str(
                    getattr(current_user_for_perms.account_type, "value", "")
                    or current_user_for_perms.account_type
                ).lower()
                == "bot"
            )

            for key, value in permissions.items():
                is_wildcard = key == "*" or key.endswith(".*")
                if is_wildcard and not allow_wildcards:
                    raise InvalidPermissionError(
                        f"Wildcard permissions are not accepted: {key}"
                    )
                if is_wildcard and allow_wildcards:
                    # Privileged path: still validate shape. "*" grants
                    # everything; "<category>.*" must name a known category
                    # so typos like "admin.everything" cannot be persisted.
                    if key != "*":
                        prefix = key[:-2]
                        if not prefix or prefix not in valid_prefixes:
                            raise InvalidPermissionError(f"Unknown permission: {key}")
                    if not isinstance(value, bool):
                        raise InvalidPermissionError(
                            f"Permission {key} must be a boolean"
                        )
                    if is_bot and value:
                        if key == "*":
                            raise InvalidPermissionError(
                                "Bots cannot have permission: *"
                            )
                        for restricted in BOT_RESTRICTED_PERMISSIONS:
                            if restricted == key or restricted.startswith(
                                key[:-2] + "."
                            ):
                                raise InvalidPermissionError(
                                    f"Bots cannot have permission: {restricted} "
                                    f"(via {key})"
                                )
                    continue
                if key not in PERMISSIONS:
                    raise InvalidPermissionError(f"Unknown permission: {key}")
                if not isinstance(value, bool):
                    raise InvalidPermissionError(f"Permission {key} must be a boolean")
                if is_bot and value and key in BOT_RESTRICTED_PERMISSIONS:
                    raise InvalidPermissionError(f"Bots cannot have permission: {key}")
            updates.append("permissions = ?")
            params.append(permissions_to_json(permissions))

        if updates:
            updates.append("updated_at = ?")
            params.append(self._get_timestamp())
            params.append(user_id)
            self._db.execute(
                f"UPDATE auth_users SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )
            invalidate_pattern("token_verify:*")
            invalidate_pattern("user_data:*")
            # The username lookup caches a full ``User`` (permissions
            # included) under a *different* prefix from ``user_data``.  Without
            # this, a grant/revoke leaves the old permission map readable for
            # the whole username-cache TTL even though the token verification
            # cache was refreshed.
            invalidate_pattern("user_by_username:*")
            from src.core.database import cache_delete

            cache_delete(f"user_profile:{user_id}")

        # W7 lockout FIX: a changed address is now unverified, so the
        # verification email for the NEW address must actually be sent —
        # otherwise the user is permanently locked out of the verified state.
        if updates and email and email_changed and self.email_sender:
            try:
                self._send_verification_email(user_id, email)
            except Exception as e:
                logger.error(
                    f"Failed to send verification email after address change "
                    f"for user {user_id}: {e}"
                )

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
                user_dict["email"] = self.crypto.decrypt_data(
                    user_dict["email_encrypted"], context=str(user_id)
                )
            except Exception:
                user_dict["email"] = None
        if user_dict.get("date_of_birth"):
            try:
                user_dict["dob_decrypted"] = self.crypto.decrypt_data(
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
        row = self._db.fetch_one(query, (user_id,))
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
        row = self._db.fetch_one(
            "SELECT id FROM auth_users WHERE username = ?", (username,)
        )
        if not row:
            return None
        return self.get_user(row["id"])

    @staticmethod
    def _profile_snapshot(profile: Dict[str, Any]) -> Dict[str, Any]:
        """Return an independent copy of a cached profile, nested values included.

        ``badges`` and ``permissions`` are mutable (list/dict) fields. A shallow
        copy would still let a caller that appends a badge or edits a permission
        mutate the object held by the cache, so copy recursively.
        """
        return copy.deepcopy(profile)

    @classmethod
    def _profile_from_cache(cls, value: Any) -> Optional[Dict[str, Any]]:
        """Return a usable cached profile dict, or ``None`` (a miss).

        A corrupt entry (unparsable JSON string, wrong shape) is discarded and
        rebuilt from the database, exactly as on the old per-id path. The dict
        is copied on the way out so every caller gets its own instance: a
        mutation (top-level or of nested ``badges``/``permissions``) cannot
        poison the cached profile seen by later readers.
        """
        if not value:
            return None
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError) as exc:
                logger.debug(f"discarding unparsable cached profile: {exc}")
                return None
        if isinstance(value, dict) and "created_at" in value:
            return cls._profile_snapshot(value)
        return None

    def get_user_profiles_bulk(self, user_ids: List[int]) -> Dict[str, Any]:
        if not user_ids:
            return {}

        # De-duplicate up front so a repeated id cannot inflate L1 probes or
        # the MGET key list. Order is preserved; callers only key on str(uid).
        seen_ids: set = set()
        unique_ids: List[int] = []
        for uid in user_ids:
            if uid not in seen_ids:
                seen_ids.add(uid)
                unique_ids.append(uid)
        user_ids = unique_ids

        result: Dict[str, Any] = {}
        missing_ids: List[int] = []

        from src.core.database import cache_get_many, cache_set, valkey_available
        from src.core.database.cache.base import mem_cache_get, mem_cache_set

        use_valkey = valkey_available()

        # Pass 1: resolve what we can from the bounded process-local L1 tier.
        # Collect the shared-cache keys for ids the L1 missed so they can all be
        # fetched together below. Keys are identical to the L1 keys (so the
        # existing ``user_profile:*`` invalidation hooks clear both tiers).
        pending_keys: List[str] = []
        pending_uids: List[int] = []
        for uid in user_ids:
            cache_key = f"user_profile:{uid}"
            cached_profile = mem_cache_get(cache_key)
            if cached_profile is not None:
                _profile_bulk_l1_stats["hits"] += 1
                usable = self._profile_from_cache(cached_profile)
                if usable is not None:
                    result[str(uid)] = usable
                else:
                    missing_ids.append(uid)
                continue

            _profile_bulk_l1_stats["misses"] += 1
            if use_valkey:
                _profile_bulk_l1_stats["valkey_gets"] += 1
                pending_keys.append(cache_key)
                pending_uids.append(uid)
            else:
                missing_ids.append(uid)

        # Pass 2: one bulk MGET for every L1 miss. A message page can resolve
        # ~100 authors, so the old one-GET-per-id loop paid that many serial
        # round trips on every cold listing. ``cache_get_many`` issues a single
        # Valkey MGET (and falls back to individual GETs only when no bulk
        # backend exists), so query cost is now independent of page size.
        if pending_keys:
            _profile_bulk_l1_stats["valkey_bulk_calls"] += 1
            bulk = cache_get_many(pending_keys) or {}
            for uid, cache_key in zip(pending_uids, pending_keys):
                usable = self._profile_from_cache(bulk.get(cache_key))
                if usable is not None:
                    result[str(uid)] = usable
                    # Seed the bounded L1 under the same key as the shared cache
                    # so the next listing resolves this id without any lookup.
                    # Store a snapshot, not ``usable`` itself: ``usable`` is
                    # returned to the caller below, and mutating it must not
                    # reach into the cached entry through the shared object.
                    mem_cache_set(
                        cache_key,
                        self._profile_snapshot(usable),
                        PROFILE_BULK_L1_TTL_SECONDS,
                    )
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
            rows = self._db.fetch_all(query, tuple(missing_ids))

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
                    "permissions": self._json_loads(row["permissions"])
                    if isinstance(row["permissions"], str)
                    else row["permissions"],
                    "account_type": row["account_type"],
                    "avatar_url": f"/api/v1/avatars/users/{user_id}",
                    "badges": badges,
                }
                result[str(user_id)] = profile

                cache_key = f"user_profile:{user_id}"
                # Seed the bounded L1 under the same key as the shared cache so
                # the next listing resolves this username without a Valkey GET
                # or a database query. Store snapshots: ``profile`` is also
                # returned to the caller, and a caller appending a badge or
                # editing permissions must not corrupt the cached profile used
                # by every later listing.
                # One snapshot serves both tiers: neither cache function mutates
                # the object it is handed, so the second deepcopy was repeated
                # work on every cold miss.
                snapshot = self._profile_snapshot(profile)
                mem_cache_set(
                    cache_key,
                    snapshot,
                    PROFILE_BULK_L1_TTL_SECONDS,
                )
                if use_valkey:
                    cache_set(cache_key, snapshot, ttl=300)

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
        rows = self._db.fetch_all(query, tuple(user_ids))

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
                    user_dict["email"] = self.crypto.decrypt_data(
                        user_dict["email_encrypted"], context=str(user_id)
                    )
                except Exception:
                    user_dict["email"] = None

            if user_dict.get("date_of_birth"):
                try:
                    user_dict["dob_decrypted"] = self.crypto.decrypt_data(
                        user_dict["date_of_birth"], context=str(user_id)
                    )
                except Exception:
                    user_dict["dob_decrypted"] = None

            result[user_id] = self._dict_to_user(user_dict)

        return result

    def grant_permission(
        self, user_id: int, permission: str, allow_wildcards: bool = False
    ) -> bool:
        """Grant a single permission to a user.

        Wildcard keys (``"*"``, ``"<category>.*"``) are rejected unless
        ``allow_wildcards=True`` is passed explicitly by a privileged
        caller (admin provisioning, self-test). Ordinary callers must
        leave the default ``False`` so a compromised/compromisable path
        can never mint admin-equivalent grants.
        """
        user = self.get_user(user_id)
        if not user:
            return False
        perms = user.permissions.copy()
        perms[permission] = True
        self.update_user(user_id, permissions=perms, allow_wildcards=allow_wildcards)
        return True
