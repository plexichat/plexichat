"""Token verification for session, bot and gateway tickets."""

import hashlib
from typing import Dict, Optional

import utils.logger as logger
from src.core.database import cached

from ...exceptions import TokenExpiredError, TokenInvalidError
from ...models import AccountType, TokenInfo
from ...permissions import permissions_from_json
from ...tokens import parse_token, verify_token_hash
from ..protocol import AuthManagerProtocol

# CORRECTNESS FIX: verify_token is wrapped in @cached, so a cache hit
# reconstructs its return value from JSON. Without registering TokenInfo on
# the reconstruction allowlist, a hit returns a PLAIN DICT and every consumer
# that touches token_info.user_id / account_locked crashes (e.g. the second
# authenticated request within the 10s verify TTL failing with a 500).
from src.core.database.cache.serialization import register_reconstructable_type

register_reconstructable_type(TokenInfo)


class VerificationMixin(AuthManagerProtocol):
    @cached(
        ttl=10,
        prefix="token_verify",
        # Never embed the raw bearer token in the cache key: hash it so that
        # anyone with cache visibility cannot recover live session tokens.
        key_builder=lambda self, token, ip_address=None, user_agent=None, is_selftest=False: (
            f"token_verify:str:{hashlib.sha256(token.encode()).hexdigest()}"
        ),
        skip_cache_if=lambda self, token, ip_address=None, user_agent=None, is_selftest=False: (
            (
                # Single-use WS tickets must NEVER be served from cache: a replay
                # within the TTL would bypass the atomic consumption below.
                isinstance(token, str) and token.startswith("wst.")
            )
            or bool(
                self._config.get("security", {}).get("token_binding", False)
                or self._config.get("security", {}).get("token_verify_rate_limit")
            )
        ),
    )
    def verify_token(
        self,
        token: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        is_selftest: bool = False,
    ) -> TokenInfo:
        security_cfg = self._config.get("security", {})
        rate_limit = security_cfg.get("token_verify_rate_limit")
        if rate_limit and ip_address and not is_selftest:
            from src.core.database.cache import check_rate_limit

            allowed, _ = check_rate_limit(
                key=f"token_verify:{ip_address}",
                limit=int(rate_limit),
                window_seconds=60,
            )
            if not allowed:
                raise TokenInvalidError("Token verification rate limit exceeded")

        parsed = parse_token(token)
        if not parsed:
            raise TokenInvalidError("Invalid token")
        if parsed["token_type"] == "session":
            return self._verify_session_token(parsed, ip_address, user_agent=user_agent)
        elif parsed["token_type"] == "bot":
            return self._verify_bot_token(parsed, ip_address)
        elif parsed["token_type"] == "wst":
            return self._verify_ws_ticket(parsed, ip_address, user_agent=user_agent)
        raise TokenInvalidError("Unknown type")

    def _verify_session_token(
        self, parsed: Dict, ip_address: Optional[str], user_agent: Optional[str] = None
    ) -> TokenInfo:
        row = self._db.fetch_one(
            "SELECT s.*, u.username, u.permissions, u.account_type, u.account_locked, u.force_username_change, u.deletion_status FROM auth_sessions s JOIN auth_users u ON s.user_id = u.id WHERE s.id = ?",
            (parsed["id"],),
        )
        if (
            not row
            or row["revoked"]
            or not verify_token_hash(parsed["secret"], row["token_hash"])
        ):
            raise TokenInvalidError("Invalid/Revoked")

        if row.get("deletion_status") == "frozen":
            logger.warning(f"Attempted use of token for frozen user {row['user_id']}")
            raise TokenInvalidError("Account is scheduled for deletion")

        if row["expires_at"] < self._get_timestamp():
            raise TokenExpiredError("Expired")

        if self._config.get("security", {}).get("token_binding", False):
            if not ip_address:
                raise TokenInvalidError("IP Binding Required")

            current_ip_index = self.crypto.fast_blind_index(ip_address, "ip_address")
            if row.get("ip_index") and row["ip_index"] != current_ip_index:
                raise TokenInvalidError("IP Binding Mismatch")

            if not user_agent:
                raise TokenInvalidError("User-Agent Binding Required")

            current_ua_index = self._ua_index(user_agent)
            if row.get("ua_index") and row["ua_index"] != current_ua_index:
                raise TokenInvalidError("User-Agent Binding Mismatch")

        now = self._get_timestamp()

        last_activity = row.get("last_activity", 0)
        should_update = (now - last_activity) > 60000
        sessions_config = self._config.get("sessions", {})
        if sessions_config.get("extend_on_activity", True):
            extend_threshold_hours = sessions_config.get("extend_threshold_hours", 24)
            extend_threshold = extend_threshold_hours * 3600 * 1000
            if (
                extend_threshold_hours == 0
                or (row["expires_at"] - now) <= extend_threshold
            ):
                should_update = True

        if should_update:
            updates = ["last_activity = ?"]
            params = [now]

            if sessions_config.get("extend_on_activity", True):
                extend_threshold_hours = sessions_config.get(
                    "extend_threshold_hours", 24
                )
                # Trusted-device sessions re-arm to their own (longer)
                # duration with their own threshold. Fallback chain:
                # trusted_extend_threshold_hours -> trusted_expire_hours ->
                # the untrusted defaults.
                trusted = bool(
                    row.get("device_id")
                    and self._device_is_trusted_by_id(row["user_id"], row["device_id"])
                )
                if trusted:
                    if (
                        sessions_config.get("trusted_extend_threshold_hours")
                        is not None
                    ):
                        extend_threshold_hours = int(
                            sessions_config["trusted_extend_threshold_hours"]
                        )
                    elif sessions_config.get("trusted_expire_hours") is not None:
                        extend_threshold_hours = int(
                            sessions_config["trusted_expire_hours"]
                        )
                extend_threshold = extend_threshold_hours * 3600 * 1000
                if (
                    extend_threshold_hours == 0
                    or (row["expires_at"] - now) <= extend_threshold
                ):
                    if (
                        trusted
                        and sessions_config.get("trusted_expire_hours") is not None
                    ):
                        expire_hours = int(sessions_config["trusted_expire_hours"])
                    else:
                        expire_hours = sessions_config.get("expire_hours", 720)
                    new_expires = now + (expire_hours * 3600 * 1000)
                    updates.append("expires_at = ?")
                    params.append(new_expires)

            params.append(row["id"])
            self._db.execute(
                f"UPDATE auth_sessions SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )

        rate_limit_tier = row.get("rate_limit_tier", "standard")
        return TokenInfo(
            valid=True,
            token_type="user",
            account_id=row["user_id"],
            user_id=row["user_id"],
            session_id=row["id"],
            username=row["username"],
            permissions=permissions_from_json(row["permissions"]),
            account_type=AccountType(row["account_type"]),
            rate_limit_tier=rate_limit_tier,
            expires_at=row["expires_at"],
            account_locked=bool(row.get("account_locked", 0)),
            force_username_change=bool(row.get("force_username_change", 0)),
            deletion_status=row.get("deletion_status", "active"),
        )

    def _verify_bot_token(self, parsed: Dict, ip_address: Optional[str]) -> TokenInfo:
        row = self._db.fetch_one(
            "SELECT * FROM auth_bots WHERE id = ?", (parsed["id"],)
        )
        if (
            not row
            or row["disabled"]
            or not verify_token_hash(parsed["secret"], row["token_hash"])
        ):
            raise TokenInvalidError("Invalid Bot")

        return TokenInfo(
            valid=True,
            token_type="bot",
            account_id=row["id"],
            user_id=row["owner_id"],
            session_id=None,
            username=row["username"],
            permissions=permissions_from_json(row["permissions"]),
            account_type=AccountType.BOT,
            rate_limit_tier="bot",
            expires_at=None,
        )
