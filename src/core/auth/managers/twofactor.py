import hmac
import json
import sys
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

import utils.logger as logger

from ..exceptions import (
    AuthError,
    UserNotFoundError,
    TokenInvalidError,
    TokenExpiredError,
    TwoFactorInvalidError,
    InvalidCredentialsError,
    AccountLockedError,
    EmailNotVerifiedError,
)
from ..models import (
    User,
    AccountType,
    AuditEventType,
    AuthResult,
    AuthStatus,
    TwoFactorSetup,
    TwoFactorChallenge,
    TwoFactorStatus,
)
from ..permissions import permissions_from_json
from ..tokens import create_2fa_challenge_token, parse_token, verify_token_hash
from .. import totp as totp_module
from src.core.database import invalidate_pattern

from ..gateway_revocation import (
    close_session_gateway_connections,
    close_user_gateway_connections,
)


from .protocol import AuthManagerProtocol


# Process-wide in-process fallback for the 2FA confirm limiter. It is only used
# when no Valkey backend is configured (single-worker deployments and tests),
# but it is keyed by user id and used to live for the whole process: every
# account that ever typed a wrong code left a bucket behind for ever. It is now
# a bounded LRU with an entry cap, an approximate byte cap and an idle TTL.
#
# SECURITY -- eviction must not weaken the brute-force guard. This dict *is*
# the TOTP guessing limiter when Valkey is down; dropping a live bucket resets
# that user's attempt count. Three rules keep the guard intact:
#   * buckets are LRU-ordered and a bucket with more live attempts is treated
#     as hotter than one with fewer, so an attacker actively guessing is never
#     chosen as the victim while colder/idle buckets are available;
#   * the bucket being updated is removed before the caps are enforced and
#     re-inserted as the most-recently-used entry in the same lock hold, so it
#     can never be evicted in the middle of its own update;
#   * a bucket's expiry is never earlier than ``last_seen + window_seconds``,
#     so idle reclamation cannot shorten a caller's configured limiter window.
_TWOFA_ATTEMPTS_MAX_ENTRIES = 4096
_TWOFA_ATTEMPTS_MAX_BYTES = 4 * 1024 * 1024
_TWOFA_ATTEMPTS_IDLE_TTL_SECONDS = 24 * 60 * 60
_TWOFA_ATTEMPTS_NODE_OVERHEAD = 128
_TWOFA_ATTEMPTS_SAMPLE_ITEMS = 64

_TWOFA_ATTEMPTS: "OrderedDict[int, List[float]]" = OrderedDict()
# user id -> (last_seen_monotonic, expires_at_monotonic, approx_bytes)
_TWOFA_ATTEMPTS_META: "Dict[int, Tuple[float, float, int]]" = {}
_TWOFA_ATTEMPTS_BYTES = 0
_TWOFA_ATTEMPTS_LOCK = threading.Lock()


def _twofa_attempts_bytes(user_id: Any, stamps: List[float]) -> int:
    """Bounded-work, conservative estimate of one bucket's retained bytes."""
    try:
        sample = stamps[:_TWOFA_ATTEMPTS_SAMPLE_ITEMS]
        largest = max((sys.getsizeof(t) for t in sample), default=32)
        return (
            sys.getsizeof(user_id)
            + sys.getsizeof(stamps)
            + sum(sys.getsizeof(t) for t in sample)
            + largest * max(0, len(stamps) - len(sample))
            + _TWOFA_ATTEMPTS_NODE_OVERHEAD
        )
    except Exception:  # pragma: no cover - fail closed on a hostile __sizeof__
        return 1024


def _reconcile_twofa_attempts_locked() -> None:
    """Rebuild bookkeeping if a test/caller cleared the registry directly."""
    global _TWOFA_ATTEMPTS_BYTES
    if _TWOFA_ATTEMPTS_META.keys() == _TWOFA_ATTEMPTS.keys():
        return
    now = time.monotonic()
    _TWOFA_ATTEMPTS_META.clear()
    _TWOFA_ATTEMPTS_BYTES = 0
    for uid, stamps in _TWOFA_ATTEMPTS.items():
        size = _twofa_attempts_bytes(uid, stamps)
        # Externally inserted buckets get the maximum idle lifetime so a test
        # double is never silently dropped as "expired".
        _TWOFA_ATTEMPTS_META[uid] = (
            now,
            now + max(_TWOFA_ATTEMPTS_IDLE_TTL_SECONDS, 1),
            size,
        )
        _TWOFA_ATTEMPTS_BYTES += size


def _drop_twofa_attempts_locked(uid: Any) -> None:
    """Remove one bucket, keeping the byte counter in sync."""
    global _TWOFA_ATTEMPTS_BYTES
    _TWOFA_ATTEMPTS.pop(uid, None)
    meta = _TWOFA_ATTEMPTS_META.pop(uid, None)
    if meta is not None:
        _TWOFA_ATTEMPTS_BYTES -= meta[2]
    if _TWOFA_ATTEMPTS_BYTES < 0:  # defensive: external mutation
        _TWOFA_ATTEMPTS_BYTES = 0


def _store_twofa_attempts_locked(
    uid: Any, stamps: List[float], now: float, window_seconds: int
) -> None:
    """Insert/replace a bucket as MRU with a window-safe expiry."""
    global _TWOFA_ATTEMPTS_BYTES
    old_meta = _TWOFA_ATTEMPTS_META.pop(uid, None)
    if old_meta is not None:
        _TWOFA_ATTEMPTS_BYTES -= old_meta[2]
    size = _twofa_attempts_bytes(uid, stamps)
    _TWOFA_ATTEMPTS[uid] = stamps
    _TWOFA_ATTEMPTS.move_to_end(uid)
    _TWOFA_ATTEMPTS_META[uid] = (
        now,
        now + max(window_seconds, _TWOFA_ATTEMPTS_IDLE_TTL_SECONDS),
        size,
    )
    _TWOFA_ATTEMPTS_BYTES += size


def _prune_twofa_attempts_locked(now: float) -> None:
    """Drop idle buckets; the TTL is the caller's window or the idle floor."""
    for uid in [k for k, meta in _TWOFA_ATTEMPTS_META.items() if meta[1] <= now]:
        _drop_twofa_attempts_locked(uid)


def _evict_twofa_attempts_locked(extra_bytes: int) -> None:
    """Enforce the entry and byte caps without dropping the bucket in hand."""
    global _TWOFA_ATTEMPTS_BYTES
    while _TWOFA_ATTEMPTS and (
        len(_TWOFA_ATTEMPTS) >= _TWOFA_ATTEMPTS_MAX_ENTRIES
        or _TWOFA_ATTEMPTS_BYTES + extra_bytes > _TWOFA_ATTEMPTS_MAX_BYTES
    ):
        # Coldest first: fewest live attempts, then least recently seen. A hot
        # guessing budget is never sacrificed to make room for idle buckets.
        candidates = [
            (len(stamps), meta[0], uid)
            for uid, stamps in _TWOFA_ATTEMPTS.items()
            if (meta := _TWOFA_ATTEMPTS_META.get(uid)) is not None
        ]
        if not candidates:  # no metadata (never happens after reconcile)
            break
        victim = min(candidates)[2]
        _drop_twofa_attempts_locked(victim)
    if _TWOFA_ATTEMPTS_BYTES < 0:  # defensive: external mutation
        _TWOFA_ATTEMPTS_BYTES = 0


def clear_twofa_attempts() -> None:
    """Drop every in-process attempt bucket (tests / config reload)."""
    global _TWOFA_ATTEMPTS_BYTES
    with _TWOFA_ATTEMPTS_LOCK:
        _TWOFA_ATTEMPTS.clear()
        _TWOFA_ATTEMPTS_META.clear()
        _TWOFA_ATTEMPTS_BYTES = 0


class TwoFactorMixin(AuthManagerProtocol):
    # Backwards-compatible alias: the process-wide store is the module-level
    # bounded LRU above. ``.clear()`` (used by the test suite) still works.
    _2fa_verify_attempts_local = _TWOFA_ATTEMPTS

    # Expired 2FA challenges are otherwise never removed: every login by a
    # 2FA-enabled account inserts a row that lives forever, so an attacker
    # with valid credentials (or anyone able to reach the login endpoint)
    # can grow ``auth_2fa_challenges`` without bound. Prune lazily on the
    # challenge-minting path, rate-limited like the TOTP replay DB cleanup so
    # a busy server never pays a full DELETE scan on every login.
    _last_challenge_cleanup: int = 0
    _challenge_cleanup_interval_ms: int = 300_000

    def _prune_expired_challenges(self, now: int) -> None:
        """Best-effort purge of expired challenge rows (rate-limited)."""
        if (
            now - TwoFactorMixin._last_challenge_cleanup
            < TwoFactorMixin._challenge_cleanup_interval_ms
        ):
            return
        TwoFactorMixin._last_challenge_cleanup = now
        try:
            self._db.execute(
                "DELETE FROM auth_2fa_challenges WHERE expires_at <= ?", (now,)
            )
        except Exception as exc:
            # Pruning is best-effort: expired rows are still rejected on read,
            # so a failure only costs space until the next successful prune.
            logger.debug(f"2FA challenge prune failed: {exc}")

    def _check_and_record_2fa_attempt(
        self, user_id: int, max_attempts: int = 5, window_seconds: int = 300
    ) -> None:
        """Rate-limit TOTP code guessing for non-challenge 2FA endpoints.

        Backed by the shared cache (Valkey primary) so the limit holds across
        all workers in a horizontally scaled deployment. SECURITY: this limiter
        fails CLOSED — if the cache backend errors while it is supposed to be
        available, attempts are refused rather than waved through.

        The in-process fallback is a bounded LRU (see module constants). A
        bucket's idle expiry is ``max(window_seconds, idle floor)``, so idle
        reclamation never shortens the configured guessing window, and the
        bucket being updated is pinned against eviction for its whole update.
        """
        from src.core.database.valkey_client import (
            get_client,
            is_available,
            ValkeyOperationError,
        )

        client = get_client()
        if client and is_available():
            full_key = f"ratelimit:auth_2fa_attempts:{user_id}"
            try:
                current = client.incr(full_key)
                if current == 1:
                    client.expire(full_key, window_seconds)
                if current > max_attempts:
                    raise TwoFactorInvalidError("Too many attempts; try again later")
                return
            except ValkeyOperationError:
                logger.error(
                    "2FA attempt limiter: cache backend error — failing closed"
                )
                raise TwoFactorInvalidError("Too many attempts; try again later")

        # No cache backend configured: bounded in-process sliding window.
        now = time.monotonic()
        with _TWOFA_ATTEMPTS_LOCK:
            _reconcile_twofa_attempts_locked()
            _prune_twofa_attempts_locked(now)

            # Recompute live attempts from the stored bucket (not a shared
            # mutable list) so a held reference cannot mutate the registry.
            previous = _TWOFA_ATTEMPTS.get(user_id, [])
            attempts = [t for t in previous if now - t < window_seconds]
            if len(attempts) >= max_attempts:
                # Even a denied attempt refreshes "last seen": an attacker
                # hammering a budget must not look cold and be evicted.
                _store_twofa_attempts_locked(user_id, attempts, now, window_seconds)
                raise TwoFactorInvalidError("Too many attempts; try again later")

            # Take the bucket out before enforcing the caps so it can never be
            # evicted in the middle of its own update.
            _drop_twofa_attempts_locked(user_id)
            attempts.append(now)
            _evict_twofa_attempts_locked(_twofa_attempts_bytes(user_id, attempts))
            _store_twofa_attempts_locked(user_id, attempts, now, window_seconds)

    def _create_2fa_challenge(
        self,
        user_id: int,
        device_id: Optional[int],
        ip: Optional[str],
        ua: Optional[str],
    ) -> TwoFactorChallenge:
        now = self._get_timestamp()
        self._prune_expired_challenges(now)
        cid = self._generate_id()
        expires = now + 300000
        token, token_hash = create_2fa_challenge_token(cid)

        ip_index = None
        ip_encrypted = None
        if ip:
            ip_index = self.crypto.blind_index(ip, "ip_address")
            ip_encrypted = self.crypto.encrypt_data(ip, context=str(cid))

        ua_index = self._ua_index(ua)
        ua_encrypted = self._encrypt_ua(ua, str(cid))

        self._db.execute(
            "INSERT INTO auth_2fa_challenges (id, user_id, token_hash, device_id, ip_index, ip_encrypted, ua_index, ua_encrypted, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cid,
                user_id,
                token_hash,
                device_id,
                ip_index,
                ip_encrypted,
                ua_index,
                ua_encrypted,
                now,
                expires,
            ),
        )
        return TwoFactorChallenge(
            id=cid,
            user_id=user_id,
            created_at=now,
            expires_at=expires,
            device_id=device_id,
            ip_address=ip,
            user_agent=ua,
            token=token,
        )

    def complete_2fa(
        self,
        challenge_token: str,
        code: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AuthResult:
        parsed = parse_token(challenge_token)
        if not parsed or parsed["token_type"] != "2fa":
            raise TokenInvalidError("Invalid challenge")
        row = self._db.fetch_one(
            "SELECT * FROM auth_2fa_challenges WHERE id = ?", (parsed["id"],)
        )
        if not row:
            raise TokenInvalidError("Expired/Used")
        if row["used"]:
            raise TokenInvalidError("Expired/Used")
        stored_hash = row.get("challenge_hash") or row.get("token_hash")
        if not stored_hash or not verify_token_hash(parsed["secret"], stored_hash):
            raise TokenInvalidError("Expired/Used")
        if row["expires_at"] < self._get_timestamp():
            raise TokenExpiredError("Expired")

        # F1: bind challenge to client context at mint time (blind indexes)
        # and enforce at completion. Reject step-up mismatch when the
        # requester's IP/UA does not match the stored blind indexes.
        # SECURITY: the stored index is a keyed hash (blind index). Guarding the
        # comparison on ``row.get("ip_index")``/``row.get("ua_index")`` made a
        # challenge minted without a recorded IP/UA (login/2FA/OAuth with no
        # context, a legacy row, or a failed index write) skip the binding
        # entirely: a stolen challenge token could then be completed from ANY
        # address, silently disabling the binding. That is the same fail-open
        # fixed for session tokens in _verify_session_token. When the caller
        # presents context but no usable stored index exists, fail closed.
        if ip_address is not None:
            try:
                stored_ip_index = row.get("ip_index")
                if not isinstance(stored_ip_index, str) or not stored_ip_index:
                    raise TokenInvalidError("Client context mismatch")
                presented_ip_index = self.crypto.blind_index(ip_address, "ip_address")
                if not hmac.compare_digest(
                    str(stored_ip_index), str(presented_ip_index)
                ):
                    logger.warning("2FA challenge IP binding mismatch")
                    raise TokenInvalidError("Client context mismatch")
            except TokenInvalidError:
                raise
            except Exception:
                logger.warning("2FA IP binding check failed", exc_info=True)
                raise TokenInvalidError("Client context mismatch")
        if user_agent is not None:
            try:
                stored_ua_index = row.get("ua_index")
                if not isinstance(stored_ua_index, str) or not stored_ua_index:
                    raise TokenInvalidError("Client context mismatch")
                presented_ua_index = self._ua_index(user_agent)
                if not isinstance(presented_ua_index, str) or not presented_ua_index:
                    raise TokenInvalidError("Client context mismatch")
                if not hmac.compare_digest(
                    str(stored_ua_index), str(presented_ua_index)
                ):
                    logger.warning("2FA challenge UA binding mismatch")
                    raise TokenInvalidError("Client context mismatch")
            except TokenInvalidError:
                raise
            except Exception:
                logger.warning("2FA UA binding check failed", exc_info=True)
                raise TokenInvalidError("Client context mismatch")

        # F2: shared per-user limiter (mirrors confirm_2fa/disable_2fa) so
        # fresh challenge tokens cannot reset the guessing budget. Fail-closed.
        user_id_for_limit = row.get("user_id")
        if user_id_for_limit is not None:
            self._check_and_record_2fa_attempt(int(user_id_for_limit))

        max_attempts = int(self._config.get("totp", {}).get("max_attempts", 3))
        if row.get("attempts", 0) >= max_attempts:
            raise TwoFactorInvalidError("Too many attempts")

        user_row = self._db.fetch_one(
            "SELECT * FROM auth_users WHERE id = ?", (row["user_id"],)
        )
        if not user_row:
            raise UserNotFoundError("User not found")

        # A challenge stays valid for minutes. Re-check the account state at
        # completion time, not just at challenge-mint time: a challenge minted
        # for a healthy account must not be exchangeable for a fresh session
        # after that account was locked, frozen, had 2FA disabled, or still has
        # an unverified email when verification is required. The HTTP route
        # performs a similar re-check, but every manager-level caller must be
        # safe on its own — otherwise those callers can mint a usable session
        # for a locked/frozen account and bypass the lockout entirely.
        if user_row.get("deletion_status") == "frozen":
            raise AccountLockedError("Account is scheduled for deletion")
        if user_row["account_locked"]:
            locked_until = user_row.get("locked_until")
            if not locked_until or locked_until > self._get_timestamp():
                raise AccountLockedError("Account locked", locked_until)
            # The lockout has elapsed. Mirror login() and clear the stale flag
            # so the session we are about to mint does not carry
            # account_locked=True into the auth middleware.
            self._db.execute(
                "UPDATE auth_users SET account_locked = 0, failed_login_attempts = 0 WHERE id = ?",
                (user_row["id"],),
            )
        accounts_config = self._config.get("accounts", {})
        if accounts_config.get("require_email_verification", False) and not bool(
            user_row.get("email_verified", 0)
        ):
            raise EmailNotVerifiedError("Email not verified")
        if not user_row.get("totp_enabled") or not user_row.get(
            "totp_secret_encrypted"
        ):
            # 2FA was disabled (or never confirmed) after the challenge was
            # minted. Fail closed instead of decrypting a NULL secret (which
            # previously raised deep in the crypto layer).
            raise TokenInvalidError("2FA no longer enabled")

        user_id = user_row["id"]
        secret = totp_module.decrypt_totp_secret(
            user_row["totp_secret_encrypted"], user_id=user_id
        )

        is_totp_valid = totp_module.verify_totp_code(
            secret,
            code,
            user_id=user_id,
            purpose="login",
            acting_user_id=user_id,
        )

        if not is_totp_valid:
            backup_hashes = (
                json.loads(user_row["backup_codes_hash"])
                if user_row["backup_codes_hash"]
                else []
            )
            valid, index = totp_module.verify_backup_code(code, backup_hashes)
            if not valid:
                self._db.execute(
                    "UPDATE auth_2fa_challenges SET attempts = attempts + 1 WHERE id = ?",
                    (row["id"],),
                )
                raise TwoFactorInvalidError("Invalid code")
            # Atomic compare-and-set consumption: two concurrent requests
            # cannot both burn the same backup code, because the update only
            # succeeds when the stored list still matches what we verified.
            remaining_hashes = backup_hashes[:index] + backup_hashes[index + 1 :]
            cursor = self._db.execute(
                "UPDATE auth_users SET backup_codes_hash = ? WHERE id = ? AND backup_codes_hash = ?",
                (
                    json.dumps(remaining_hashes),
                    user_id,
                    user_row["backup_codes_hash"],
                ),
            )
            if getattr(cursor, "rowcount", 0) == 0:
                # The same code was consumed concurrently; treat as a failure.
                self._db.execute(
                    "UPDATE auth_2fa_challenges SET attempts = attempts + 1 WHERE id = ?",
                    (row["id"],),
                )
                raise TwoFactorInvalidError("Invalid code")

        # Atomic single-use consumption: the conditional update prevents two
        # concurrent requests from both completing with the same code.
        cursor = self._db.execute(
            "UPDATE auth_2fa_challenges SET used = 1, attempts = 0 WHERE id = ? AND used = 0",
            (row["id"],),
        )
        if getattr(cursor, "rowcount", 0) == 0:
            raise TokenInvalidError("Expired/Used")

        ip_address = None
        try:
            if row.get("ip_encrypted"):
                ip_address = self.crypto.decrypt_data(
                    row["ip_encrypted"], context=str(row["id"])
                )
        except Exception:
            ip_address = None

        user_agent = None
        try:
            if row.get("ua_encrypted"):
                user_agent = self.crypto.decrypt_data(
                    row["ua_encrypted"], context=str(row["id"])
                )
        except Exception:
            user_agent = None

        session = self._create_session(
            user_id, row["device_id"], ip_address, user_agent
        )
        email = (
            self.crypto.decrypt_data(user_row["email_encrypted"], context=str(user_id))
            if user_row["email_encrypted"]
            else None
        )
        user_obj = User(
            id=user_id,
            account_type=AccountType(user_row["account_type"]),
            username=user_row["username"],
            email=email,
            permissions=permissions_from_json(user_row["permissions"]),
            created_at=user_row["created_at"],
            updated_at=user_row["updated_at"],
            force_username_change=bool(user_row.get("force_username_change", 0)),
        )
        return AuthResult(
            status=AuthStatus.SUCCESS,
            token=session.token,
            user=user_obj,
            session=session,
        )

    def setup_2fa(self, user_id: int) -> TwoFactorSetup:
        user = self._db.fetch_one(
            "SELECT username, totp_enabled FROM auth_users WHERE id = ?", (user_id,)
        )
        if not user:
            raise UserNotFoundError("User not found")
        if user["totp_enabled"]:
            raise AuthError("2FA already enabled")
        secret = totp_module.generate_totp_secret()
        backup_codes = totp_module.generate_backup_codes()
        self._db.execute(
            "UPDATE auth_users SET totp_secret_encrypted = ?, backup_codes_hash = ? WHERE id = ?",
            (
                totp_module.encrypt_totp_secret(secret),
                json.dumps(totp_module.hash_backup_codes(backup_codes)),
                user_id,
            ),
        )
        return TwoFactorSetup(
            secret=secret,
            qr_uri=totp_module.generate_totp_uri(secret, user["username"], "Plexichat"),
            backup_codes=backup_codes,
            issuer="Plexichat",
            username=user["username"],
        )

    def confirm_2fa(
        self,
        user_id: int,
        code: str,
        except_session_id: Optional[int] = None,
    ) -> bool:
        user = self._db.fetch_one(
            "SELECT totp_secret_encrypted FROM auth_users WHERE id = ?", (user_id,)
        )
        if not user:
            raise UserNotFoundError("User not found")
        if not user.get("totp_secret_encrypted"):
            raise AuthError("2FA not initiated")
        secret = totp_module.decrypt_totp_secret(
            user["totp_secret_encrypted"], user_id=user_id
        )
        self._check_and_record_2fa_attempt(user_id)
        ok = totp_module.verify_totp_code(
            secret,
            code,
            user_id=user_id,
            purpose="setup_2fa",
            acting_user_id=user_id,
        )
        if not ok:
            raise TwoFactorInvalidError("Invalid code")

        # Enabling 2FA is a credential transition just like disabling it: every
        # session minted so far was authenticated with the password alone. If
        # those sessions survive, a bearer token stolen before the switch keeps
        # full access and the new second factor protects nothing. Revoke them
        # BEFORE flipping totp_enabled so a revocation failure cannot leave a
        # "2FA enabled" account with live pre-2FA sessions (fail closed). The
        # session that just proved possession of the code may be preserved.
        if except_session_id is not None:
            self._db.execute(
                "UPDATE auth_sessions SET revoked = 1 WHERE user_id = ? AND revoked = 0 AND id != ?",
                (user_id, except_session_id),
            )
        else:
            self._db.execute(
                "UPDATE auth_sessions SET revoked = 1 WHERE user_id = ? AND revoked = 0",
                (user_id,),
            )
        # A device marked trusted before 2FA existed skips the challenge on the
        # next password login, so leaving that trust intact would make the
        # freshly enabled second factor protect nothing against a stolen/lost
        # remembered device. Retire trusted devices as part of the same
        # credential transition (password change/reset already do this). This
        # runs before totp_enabled is flipped, so a cleanup failure aborts the
        # enable rather than leaving 2FA "on" with a live bypass (fail closed).
        self._invalidate_trusted_devices(user_id)
        # A just-revoked session must not keep answering from the verify cache.
        invalidate_pattern("token_verify:*")
        # Pre-2FA sessions may still own live gateway sockets that remain
        # authenticated server-side. Close them as part of the credential
        # transition. The confirming session's socket is closed too because
        # the connection does not record the auth-session id; forcing a fresh
        # authenticated handshake here is the fail-closed choice.
        close_user_gateway_connections(user_id, "Two-factor enabled")
        self._db.execute(
            "UPDATE auth_users SET totp_enabled = 1 WHERE id = ?", (user_id,)
        )
        self._log_audit(AuditEventType.TWO_FACTOR_ENABLED, user_id, True)
        try:
            from src.core.events.gateway_emit import emit_security_alert

            emit_security_alert(user_id, "two_factor_enabled")
        except Exception as e:
            # Non-raising by design (gateway emission must not break the
            # 2FA flow), but surface at WARNING so a broken security-alert
            # path is visible at default log levels.
            logger.warning(f"Failed to emit 2FA-enabled security alert: {e}")
        invalidate_pattern("user_data:*")
        return True

    def disable_2fa(
        self,
        user_id: int,
        password: str,
        code: str,
        except_session_id: Optional[int] = None,
    ) -> bool:
        user = self._db.fetch_one(
            "SELECT password_hash, totp_secret_encrypted, totp_enabled FROM auth_users WHERE id = ?",
            (user_id,),
        )
        if not user:
            raise UserNotFoundError("User not found")
        if not user.get("totp_enabled") or not user.get("totp_secret_encrypted"):
            raise AuthError("2FA not enabled")
        if not self.crypto.verify_password(password, user["password_hash"]):
            raise InvalidCredentialsError("Invalid password")
        secret = totp_module.decrypt_totp_secret(
            user["totp_secret_encrypted"], user_id=user_id
        )
        self._check_and_record_2fa_attempt(user_id)
        ok = totp_module.verify_totp_code(
            secret,
            code,
            user_id=user_id,
            purpose="disable_2fa",
            acting_user_id=user_id,
        )
        if not ok:
            raise TwoFactorInvalidError("Invalid code")
        self._db.execute(
            "UPDATE auth_users SET totp_enabled = 0, totp_secret_encrypted = NULL, backup_codes_hash = NULL WHERE id = ?",
            (user_id,),
        )
        invalidate_pattern("user_data:*")
        # F8: emit audit log entry for 2FA disable; policy: revoke other
        # sessions or at least re-challenge. We emit audit and revoke all
        # other sessions so that previously trusted sessions cannot bypass
        # the now-disabled second factor without re-authentication.
        self._log_audit(AuditEventType.TWO_FACTOR_DISABLED, user_id, True)
        to_close: list = []
        try:
            # Policy: revoke all other sessions so previously trusted sessions
            # cannot bypass the now-disabled second factor. Preserve the
            # current session if the caller supplied it (route passes
            # current_user.session_id).
            # Capture the sessions about to be revoked so their sockets can be
            # closed individually. A blanket user-wide close would also kill
            # the surviving current session's socket.
            if except_session_id is not None:
                to_close = self._db.fetch_all(
                    "SELECT id FROM auth_sessions WHERE user_id = ? AND revoked = 0 AND id != ?",
                    (user_id, except_session_id),
                )
                self._db.execute(
                    "UPDATE auth_sessions SET revoked = 1 WHERE user_id = ? AND revoked = 0 AND id != ?",
                    (user_id, except_session_id),
                )
            else:
                to_close = self._db.fetch_all(
                    "SELECT id FROM auth_sessions WHERE user_id = ? AND revoked = 0",
                    (user_id,),
                )
                self._db.execute(
                    "UPDATE auth_sessions SET revoked = 1 WHERE user_id = ? AND revoked = 0",
                    (user_id,),
                )
            invalidate_pattern("token_verify:*")
        except Exception as e:
            # Security-relevant: if revocation fails, previously trusted
            # sessions survive the 2FA disable. Stay non-raising (the
            # disable itself succeeded) but log at WARNING so the gap is
            # visible at default levels instead of hidden at DEBUG.
            logger.warning(
                f"Failed to revoke sessions on 2FA disable for user {user_id}: {e}",
                exc_info=True,
            )
        # A SECURITY_ALERT is a client-side hint only. Close the sockets of the
        # sessions we just revoked, by auth-session id, so the surviving
        # current session (when except_session_id was supplied) stays usable.
        for row in to_close:
            close_session_gateway_connections(
                user_id, int(row["id"]), "Session revoked"
            )
        try:
            from src.core.events.gateway_emit import emit_security_alert

            emit_security_alert(user_id, "two_factor_disabled")
        except Exception as e:
            # Same non-raising contract as the enabled path: log loudly.
            logger.warning(f"Failed to emit 2FA-disabled security alert: {e}")
        return True

    def regenerate_backup_codes(self, user_id: int, password: str) -> List[str]:
        user = self._db.fetch_one(
            "SELECT password_hash, totp_enabled FROM auth_users WHERE id = ?",
            (user_id,),
        )
        if not user:
            raise UserNotFoundError("User not found")
        if not user.get("totp_enabled"):
            raise AuthError("2FA not enabled")
        if not self.crypto.verify_password(password, user["password_hash"]):
            raise InvalidCredentialsError("Invalid password")
        codes = totp_module.generate_backup_codes()
        self._db.execute(
            "UPDATE auth_users SET backup_codes_hash = ? WHERE id = ?",
            (json.dumps(totp_module.hash_backup_codes(codes)), user_id),
        )
        return codes

    def get_2fa_status(self, user_id: int) -> TwoFactorStatus:
        row = self._db.fetch_one(
            "SELECT totp_enabled, backup_codes_hash FROM auth_users WHERE id = ?",
            (user_id,),
        )
        if not row:
            raise UserNotFoundError("User not found")
        codes = json.loads(row["backup_codes_hash"]) if row["backup_codes_hash"] else []
        return TwoFactorStatus(
            enabled=bool(row["totp_enabled"]), backup_codes_remaining=len(codes)
        )
