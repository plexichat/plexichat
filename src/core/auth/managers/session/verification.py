"""Token verification for session, bot and gateway tickets."""

import hmac
from typing import Dict, Optional

import utils.logger as logger
from src.core.database import cached

from ...exceptions import TokenExpiredError, TokenInvalidError
from ...models import AccountType, TokenInfo
from ...permissions import has_permission, permissions_from_json
from ...tokens import (
    is_expired,
    is_malformed_token_input,
    parse_token,
    safe_token_binding_key,
    safe_token_cache_key,
    verify_token_hash,
)
from ..protocol import AuthManagerProtocol

# CORRECTNESS FIX: the cached verifier reconstructs its return value from JSON
# on a hit. Without registering TokenInfo on the reconstruction allowlist, a
# hit returns a PLAIN DICT and every consumer that touches
# token_info.user_id / account_locked crashes (e.g. the second authenticated
# request within the 10s verify TTL failing with a 500).
from src.core.database.cache.serialization import register_reconstructable_type

register_reconstructable_type(TokenInfo)


# The trusted-device flag used by the session-extension path lives on an
# ``auth_devices`` row reachable from the session's own ``device_id``.  Reading
# it as a scalar subquery here folds what used to be a second round trip
# (``_device_is_trusted_by_id``) into the single session/user SELECT, so an
# extended session no longer pays two DB reads per verification.  The
# ``user_id`` predicate keeps the lookup bound to the session's owner, exactly
# as the old helper was.
_SESSION_VERIFY_SQL = (
    "SELECT s.*, u.username, u.permissions, u.account_type, u.account_locked, "
    "u.force_username_change, u.deletion_status, "
    "(SELECT d.trusted FROM auth_devices d "
    " WHERE d.id = s.device_id AND d.user_id = s.user_id) AS device_trusted "
    "FROM auth_sessions s JOIN auth_users u ON s.user_id = u.id WHERE s.id = ?"
)


class VerificationMixin(AuthManagerProtocol):
    def verify_token(
        self,
        token: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        is_selftest: bool = False,
    ) -> TokenInfo:
        """Verify a credential and return its ``TokenInfo``.

        The bounded, short-TTL cache lives on :meth:`_verify_token`. Everything
        that can change between two calls carrying the same credential stays
        *outside* that cache and runs on every call:

        * malformed input fails closed before any cache-key work;
        * the verification rate limit is enforced before the cache lookup, so a
          cache hit can never be used to bypass throttling;
        * IP/User-Agent binding is folded into the cache key, so a result
          verified in one network context is never replayed in another;
        * revocation / privilege changes drop the shared ``token_verify:*``
          keyspace after every state write, so a cached ``TokenInfo`` cannot
          outlive a revoke; and
        * the cache backend itself fails closed (errors fall through to a fresh
          DB verification).
        """
        # Fail closed on missing/malformed auth context before any lookup.
        if is_malformed_token_input(token):
            raise TokenInvalidError("Invalid token")
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

        result = self._verify_token(token, ip_address, user_agent)

        # The memoised verifier caches the whole verdict for a short TTL. Its
        # expiry check runs only on a cache *miss*, so an expiry reached inside
        # that window would otherwise keep authenticating until eviction.
        # Re-check the cached verdict's expiry on every call (mirroring
        # verify_api_access_token) so an expired credential fails closed even
        # on a hit. Bot tickets carry no expiry (None) and are unaffected.
        expires_at = getattr(result, "expires_at", None)
        if expires_at is not None and is_expired(expires_at):
            raise TokenExpiredError("Expired")
        return result

    @cached(
        ttl=10,
        prefix="token_verify",
        # Revocation/privilege changes invalidate this namespace, dropping every
        # verdict at once. Without coalescing, all concurrent requests landing
        # on that cold miss each re-run the session SELECT + constant-time hash
        # check -- a single invalidation stampedes the DB. Collapse the miss so
        # one caller verifies and the rest await the same credential-keyed
        # verdict. (Availability only; authz is unchanged.)
        single_flight=True,
        # Never embed the raw bearer token in the cache key: hash it so that
        # anyone with cache visibility cannot recover live session tokens. The
        # binding suffix keeps a cached result scoped to the exact IP/UA it was
        # verified for. Previously both ``token_binding`` and the rate-limit
        # setting disabled this cache wholesale, which meant every repeated
        # verification of the same token within a request/connection paid for a
        # full DB lookup + constant-time hash comparison again.
        key_builder=lambda self, token, ip_address=None, user_agent=None: (
            safe_token_cache_key(token) + safe_token_binding_key(ip_address, user_agent)
        ),
        # Only single-use WS tickets and malformed input bypass the cache. A
        # replay of a ticket within the TTL would bypass its atomic consumption;
        # binding and rate limiting are handled above / in the key builder, so
        # they no longer need to turn caching off.
        skip_cache_if=lambda self, token, ip_address=None, user_agent=None: (
            is_malformed_token_input(token)
            or (isinstance(token, str) and token.startswith("wst."))
        ),
    )
    def _verify_token(
        self,
        token: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> TokenInfo:
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
        # Defense in depth: never let a differently-prefixed credential be
        # interpreted as a session token if this verifier is called directly.
        if parsed.get("token_type") != "session":
            raise TokenInvalidError("Token type mismatch")
        # The session/user SELECT now also resolves the trusted-device flag, so
        # keep the schema (trusted column) current before issuing it. The verdict
        # is memoized per db, so this is a no-op after the first call.
        self._ensure_trusted_device_columns()
        row = self._db.fetch_one(_SESSION_VERIFY_SQL, (parsed["id"],))
        if (
            not row
            or row["revoked"]
            or not verify_token_hash(parsed["secret"], row["token_hash"])
        ):
            raise TokenInvalidError("Invalid/Revoked")

        if row.get("deletion_status") == "frozen":
            logger.warning(f"Attempted use of token for frozen user {row['user_id']}")
            raise TokenInvalidError("Account is scheduled for deletion")

        now = self._get_timestamp()
        if is_expired(row["expires_at"], now):
            raise TokenExpiredError("Expired")

        if self._config.get("security", {}).get("token_binding", False):
            if not ip_address:
                raise TokenInvalidError("IP Binding Required")

            # SECURITY: binding is MANDATORY when enabled. A session row without
            # a stored ip_index (login/2FA/OAuth with no IP, a legacy row, or a
            # failed IP decryption) used to skip the comparison entirely because
            # it was guarded by ``if row.get("ip_index")``. That silently turned
            # token_binding off for exactly those sessions: a stolen bearer
            # token could be replayed from any address. Missing/corrupt binding
            # material now fails closed, and comparisons are constant-time.
            stored_ip_index = row.get("ip_index")
            if not isinstance(stored_ip_index, str) or not stored_ip_index:
                raise TokenInvalidError("IP Binding Required")
            current_ip_index = self.crypto.fast_blind_index(ip_address, "ip_address")
            if not hmac.compare_digest(stored_ip_index, current_ip_index):
                raise TokenInvalidError("IP Binding Mismatch")

            if not user_agent:
                raise TokenInvalidError("User-Agent Binding Required")

            stored_ua_index = row.get("ua_index")
            if not isinstance(stored_ua_index, str) or not stored_ua_index:
                raise TokenInvalidError("User-Agent Binding Required")
            current_ua_index = self._ua_index(user_agent)
            if not hmac.compare_digest(stored_ua_index, current_ua_index):
                raise TokenInvalidError("User-Agent Binding Mismatch")

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
                trusted = bool(row.get("device_id") and row.get("device_trusted"))
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
        if parsed.get("token_type") != "bot":
            raise TokenInvalidError("Token type mismatch")
        row = self._db.fetch_one(
            "SELECT * FROM auth_bots WHERE id = ?", (parsed["id"],)
        )
        if (
            not row
            or row["disabled"]
            or not verify_token_hash(parsed["secret"], row["token_hash"])
        ):
            raise TokenInvalidError("Invalid Bot")

        # A bot's permission map is a DELEGATION of its owner's authority, not
        # an independent grant. ``create_bot``/``update_bot_permissions`` only
        # check that the owner holds a scope at *write* time; an admin demotion
        # afterwards used to leave the stored map unchanged, so the bot token
        # kept exercising scopes its owner had lost. Enforce the ceiling on
        # every verification instead: the bot's effective permissions are the
        # intersection of its stored map with the owner's CURRENT grants.
        # Fail closed when the owner row is missing or its permission payload
        # is not a well-formed map (a dangling owner must not authenticate).
        owner_row = self._db.fetch_one(
            "SELECT permissions FROM auth_users WHERE id = ?", (row["owner_id"],)
        )
        if not owner_row:
            raise TokenInvalidError("Invalid Bot")
        owner_perms = permissions_from_json(owner_row["permissions"])
        bot_perms = permissions_from_json(row["permissions"])
        if not isinstance(owner_perms, dict) or not isinstance(bot_perms, dict):
            raise TokenInvalidError("Invalid Bot")
        effective_perms = {
            perm: allowed
            for perm, allowed in bot_perms.items()
            if not allowed or has_permission(owner_perms, perm)
        }

        return TokenInfo(
            valid=True,
            token_type="bot",
            account_id=row["id"],
            user_id=row["owner_id"],
            session_id=None,
            username=row["username"],
            permissions=effective_perms,
            account_type=AccountType.BOT,
            rate_limit_tier="bot",
            expires_at=None,
        )
