import secrets
import ipaddress
import hmac
import hashlib
from typing import Optional, List, Dict, Any, Tuple

import utils.logger as logger
from src.core.bounded_lru import BoundedLRU
from src.core.database import cached, invalidate_pattern

from ..models import AccessToken, AuditEventType
from ..exceptions import UserNotFoundError
from ..tokens import is_expired, MAX_TOKEN_LENGTH


from .protocol import AuthManagerProtocol


# A caller-supplied API access token must carry at least the entropy of the
# server-generated default (``secrets.token_urlsafe(24)`` -> 32 urlsafe chars).
# Every other credential-setting path validates before persisting a credential;
# without this floor an internal caller that forwards a value could install a
# short, pre-known or brute-forceable permanent credential. The route layer's
# "reject caller-supplied tokens" check is not a substitute: it only guards one
# of the manager's callers and a second caller can (and does) omit it.
_MIN_CUSTOM_ACCESS_TOKEN_LENGTH = 32


# ---- Bounded, short-TTL API-token verification cache ------------------------
# ``verify_api_access_token`` runs on every authenticated request (auth
# middleware). Each call used to re-derive the blind index, issue a SELECT for
# the token row, decrypt/constant-time-compare the secret, and issue a second
# SELECT for its scopes. None of that work depends on the request path/method,
# so repeated verification of the same credential paid for it over and over.
#
# The cache below memoises only that *read-only introspection* (a small verdict
# dict). The mutable side effects -- last-used/use-count updates and the usage
# event INSERT -- deliberately stay OUTSIDE the cache and still run on every
# call, so usage accounting and auditing are unchanged.
#
# Security properties are preserved:
#   * the key is a SHA-256 digest of the exact credential, never the token;
#   * the presented IP/User-Agent are folded in as a second digest, so a verdict
#     resolved for one network context is never replayed in another (scope/IP
#     binding);
#   * every revocation/revoke/update/scope mutation clears the cache eagerly,
#     and the short TTL is a backstop so no verdict outlives a state change;
#   * expiry is re-evaluated against the current clock on EVERY call, including
#     a cache hit, so a token cannot ride a warm verdict past its expiry.
_API_TOKEN_VERIFY_MAX_ENTRIES = 2048
_API_TOKEN_VERIFY_MAX_BYTES = 256 * 1024
_API_TOKEN_VERIFY_TTL_SECONDS = 10.0

_API_TOKEN_VERIFY_CACHE = BoundedLRU(
    max_entries=_API_TOKEN_VERIFY_MAX_ENTRIES,
    max_bytes=_API_TOKEN_VERIFY_MAX_BYTES,
    ttl_seconds=_API_TOKEN_VERIFY_TTL_SECONDS,
)
# Distinct from any possible verdict (always a dict) so a cached ``False``-like
# verdict is not confused with a miss.
_API_TOKEN_VERIFY_MISS = object()


def _api_token_verify_key(token: str, ip_address, user_agent) -> Tuple[str, str]:
    """Hash-only cache key scoped to the exact credential and binding context.

    Raw tokens, IPs and user agents never enter the keyspace; only fixed-width
    SHA-256 digests are retained, so cache visibility cannot recover a live
    credential or a caller's network context.
    """
    token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    binding = f"{ip_address or ''}\x00{user_agent or ''}"
    binding_digest = hashlib.sha256(binding.encode("utf-8")).hexdigest()
    return token_digest, binding_digest


def _invalidate_api_token_verify_cache() -> None:
    """Drop every cached verification verdict (full invalidation hook).

    Called after any mutation that can change a verdict -- revocation,
    update, rotation or scope changes -- so a stale decision cannot survive.
    """
    _API_TOKEN_VERIFY_CACHE.clear()


class ApiTokenMixin(AuthManagerProtocol):
    def create_api_access_token(
        self,
        name: Optional[str],
        created_by: Optional[int],
        token_value: Optional[str] = None,
        description: Optional[str] = None,
        expires_at: Optional[int] = None,
        scope_mode: str = "none",
    ) -> AccessToken:
        scope_mode = self._normalize_access_token_scope_mode(scope_mode)
        token_id = self._generate_id()
        token = token_value.strip() if token_value else None
        if not token:
            token = secrets.token_urlsafe(24)
        elif (
            len(token) < _MIN_CUSTOM_ACCESS_TOKEN_LENGTH
            or len(token) > MAX_TOKEN_LENGTH
        ):
            # Fail closed: never persist a credential weaker than the generated
            # default or large enough to defeat the shared token bound.
            raise ValueError(
                "API access token value must be between "
                f"{_MIN_CUSTOM_ACCESS_TOKEN_LENGTH} and {MAX_TOKEN_LENGTH} characters"
            )
        token_index = self.crypto.fast_blind_index(token, "api_access_token")
        existing = self._db.fetch_one(
            "SELECT id FROM auth_api_access_tokens WHERE token_index = ?",
            (token_index,),
        )
        if existing:
            raise ValueError("API access token already exists")
        token_encrypted = self.crypto.encrypt_data(token, context=str(token_id))
        now = self._get_timestamp()
        self._db.execute(
            """INSERT INTO auth_api_access_tokens
               (id, name, description, token_index, token_encrypted, created_by, created_at, expires_at, scope_mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                token_id,
                name,
                description,
                token_index,
                token_encrypted,
                created_by,
                now,
                expires_at,
                scope_mode,
            ),
        )
        invalidate_pattern("access_token_required*")
        _invalidate_api_token_verify_cache()
        row = self._db.fetch_one(
            "SELECT * FROM auth_api_access_tokens WHERE id = ?", (token_id,)
        )
        access_token = self._row_to_access_token(row)
        access_token.token = token
        return access_token

    def list_api_access_tokens(
        self, include_revoked: bool = True, limit: int = 200, offset: int = 0
    ) -> List[AccessToken]:
        # The token table is growable by any admin holding the token scope;
        # clamp the page so one listing cannot materialise the whole table.
        limit = max(1, min(int(limit), 500))
        offset = max(0, min(int(offset), 1_000_000))
        now = self._get_timestamp()
        if include_revoked:
            rows = self._db.fetch_all(
                "SELECT * FROM auth_api_access_tokens ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        else:
            rows = self._db.fetch_all(
                """SELECT * FROM auth_api_access_tokens
                   WHERE revoked = 0 AND (expires_at IS NULL OR expires_at > ?)
                   ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (now, limit, offset),
            )
        # An empty page needs no aggregates (and keeps the final DB call on
        # this path the bounded token SELECT itself).
        if not rows:
            return []
        # Each row needs two usage aggregates over auth_api_access_token_events.
        # Resolving them per row (inside _row_to_access_token) made a page of N
        # tokens cost N extra COUNT round trips. The page is already bounded
        # (limit <= 500) and authorized; resolve every token's aggregates in one
        # GROUP BY query over exactly those token ids so no other token's events
        # can be pulled in.
        token_ids = [row["id"] for row in rows]
        placeholders = ", ".join("?" for _ in token_ids)
        summaries: Dict[int, Dict[str, Any]] = {}
        try:
            agg_rows = self._db.fetch_all(
                f"""SELECT token_id,
                           COUNT(DISTINCT ip_index) AS distinct_ip_count,
                           SUM(CASE WHEN allowed = 0 THEN 1 ELSE 0 END) AS denied_count_total
                    FROM auth_api_access_token_events
                    WHERE token_id IN ({placeholders})
                    GROUP BY token_id""",
                tuple(token_ids),
            )
        except Exception:  # pragma: no cover - enrichment is best effort
            agg_rows = []
        for row in agg_rows or []:
            if isinstance(row, dict):
                tid, distinct, denied = (
                    row.get("token_id"),
                    row.get("distinct_ip_count"),
                    row.get("denied_count_total"),
                )
            else:
                tid = row[0] if len(row) > 0 else None
                distinct = row[1] if len(row) > 1 else None
                denied = row[2] if len(row) > 2 else None
            if tid is not None:
                summaries[int(tid)] = {
                    "distinct_ip_count": distinct,
                    "denied_count_total": denied,
                }
        return [
            self._row_to_access_token(
                row, distinct_summary=summaries.get(int(row["id"]), {})
            )
            for row in rows
        ]

    def get_api_access_token(
        self,
        token_id: int,
        *,
        usage_summary: Optional[Dict[str, Any]] = None,
    ) -> Optional[AccessToken]:
        """Read a token row and render it, reusing ``usage_summary`` if given.

        The usage summary (``COUNT(DISTINCT ip_index)`` / denied count over the
        token's events) is part of every rendered :class:`AccessToken`. A caller
        that already fetched that single summary row -- e.g. the admin detail
        endpoint, which also emits the top-level counters -- can pass it here
        so the row converter does not issue an identical second SELECT.
        """
        row = self._db.fetch_one(
            "SELECT * FROM auth_api_access_tokens WHERE id = ?", (token_id,)
        )
        if not row:
            return None
        return self._row_to_access_token(row, distinct_summary=usage_summary)

    def update_api_access_token(
        self,
        token_id: int,
        updated_by: Optional[int],
        name: Optional[str] = None,
        description: Optional[str] = None,
        expires_at: Optional[int] = None,
        clear_expiry: bool = False,
        scope_mode: Optional[str] = None,
    ) -> Optional[AccessToken]:
        # Fetch full row upfront so we can construct the result from memory
        # instead of doing a second SELECT after the UPDATE.
        row = self._db.fetch_one(
            "SELECT * FROM auth_api_access_tokens WHERE id = ?", (token_id,)
        )
        if not row:
            return None
        row = dict(row)

        updates: List[str] = []
        params: List[Any] = []
        if name is not None:
            updates.append("name = ?")
            params.append(name)
            row["name"] = name
        if description is not None:
            updates.append("description = ?")
            params.append(description)
            row["description"] = description
        if clear_expiry:
            updates.append("expires_at = NULL")
            row["expires_at"] = None
        elif expires_at is not None:
            updates.append("expires_at = ?")
            params.append(expires_at)
            row["expires_at"] = expires_at
        if scope_mode is not None:
            normalized = self._normalize_access_token_scope_mode(scope_mode)
            updates.append("scope_mode = ?")
            params.append(normalized)
            row["scope_mode"] = normalized
        if not updates:
            return self._row_to_access_token(row)

        params.append(token_id)
        self._db.execute(
            f"UPDATE auth_api_access_tokens SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )
        invalidate_pattern("access_token_required*")
        _invalidate_api_token_verify_cache()
        self._log_audit(
            AuditEventType.SECURITY_SETTINGS_UPDATED,
            None,
            True,
            details={
                "access_token_id": token_id,
                "fields": updates,
                "admin_id": updated_by,
            },
        )
        if updated_by:
            try:
                from src.core.events.gateway_emit import emit_security_alert

                emit_security_alert(
                    updated_by,
                    "security_settings_updated",
                    detail={"scope": "api_access_token", "action": "update"},
                )
            except Exception as exc:
                # The token update is already committed; a failed audit write
                # must not undo it, but it must stay traceable.
                logger.debug(f"api token update audit failed: {exc}")
        # Reuse the in-memory row we already fetched, avoiding a second SELECT
        return self._row_to_access_token(row)

    def revoke_api_access_token(self, token_id: int, revoked_by: Optional[int]) -> bool:
        now = self._get_timestamp()
        cursor = self._db.execute(
            "UPDATE auth_api_access_tokens SET revoked = 1, revoked_at = ?, revoked_by = ? WHERE id = ? AND revoked = 0",
            (now, revoked_by, token_id),
        )
        if cursor.rowcount > 0:
            invalidate_pattern("access_token_required*")
            _invalidate_api_token_verify_cache()
            self._log_audit(
                AuditEventType.SECURITY_SETTINGS_UPDATED,
                None,
                True,
                details={
                    "access_token_id": token_id,
                    "action": "revoke",
                    "admin_id": revoked_by,
                },
            )
        if revoked_by:
            try:
                from src.core.events.gateway_emit import emit_security_alert

                emit_security_alert(
                    revoked_by,
                    "security_settings_updated",
                    detail={"scope": "api_access_token", "action": "revoke"},
                )
            except Exception as exc:
                logger.debug(f"api token revoke audit failed: {exc}")
        return cursor.rowcount > 0

    def unrevoke_api_access_token(
        self, token_id: int, unrevoked_by: Optional[int]
    ) -> bool:
        cursor = self._db.execute(
            "UPDATE auth_api_access_tokens SET revoked = 0, revoked_at = NULL, revoked_by = NULL WHERE id = ? AND revoked = 1",
            (token_id,),
        )
        if cursor.rowcount > 0:
            invalidate_pattern("access_token_required*")
            _invalidate_api_token_verify_cache()
            self._log_audit(
                AuditEventType.SECURITY_SETTINGS_UPDATED,
                None,
                True,
                details={
                    "access_token_id": token_id,
                    "action": "unrevoke",
                    "admin_id": unrevoked_by,
                },
            )
        if unrevoked_by:
            try:
                from src.core.events.gateway_emit import emit_security_alert

                emit_security_alert(
                    unrevoked_by,
                    "security_settings_updated",
                    detail={"scope": "api_access_token", "action": "unrevoke"},
                )
            except Exception as exc:
                logger.debug(f"api token unrevoke audit failed: {exc}")
        return cursor.rowcount > 0

    def rotate_api_access_token(
        self,
        token_id: int,
        rotated_by: Optional[int],
        token_value: Optional[str] = None,
    ) -> Optional[AccessToken]:
        existing = self._db.fetch_one(
            "SELECT * FROM auth_api_access_tokens WHERE id = ? AND revoked = 0",
            (token_id,),
        )
        if not existing:
            return None

        new_token = self.create_api_access_token(
            name=existing["name"],
            created_by=rotated_by,
            token_value=token_value,
            description=existing["description"],
            expires_at=existing["expires_at"],
            scope_mode=existing["scope_mode"] or "none",
        )
        for scope in self.list_api_access_token_scopes(token_id):
            self.add_api_access_token_scope(
                new_token.id,
                scope["scope_type"],
                scope["value"],
                rotated_by,
            )
        self.revoke_api_access_token(token_id, rotated_by)
        self._log_audit(
            AuditEventType.SECURITY_SETTINGS_UPDATED,
            None,
            True,
            details={
                "action": "rotate_access_token",
                "old_access_token_id": token_id,
                "new_access_token_id": new_token.id,
                "admin_id": rotated_by,
            },
        )
        if rotated_by:
            try:
                from src.core.events.gateway_emit import emit_security_alert

                emit_security_alert(
                    rotated_by,
                    "security_settings_updated",
                    detail={"scope": "api_access_token", "action": "rotate"},
                )
            except Exception as exc:
                logger.debug(f"api token rotate audit failed: {exc}")
        return new_token

    def add_api_access_token_scope(
        self,
        token_id: int,
        scope_type: str,
        value: str,
        created_by: Optional[int],
    ) -> Dict[str, Any]:
        normalized_type, normalized_value = self._normalize_access_token_scope(
            scope_type, value
        )
        token_exists = self._db.fetch_one(
            "SELECT id FROM auth_api_access_tokens WHERE id = ?",
            (token_id,),
        )
        if not token_exists:
            raise ValueError("Access token not found")
        scope_id = self._generate_id()
        created_at = self._get_timestamp()
        self._db.insert_or_ignore(
            "auth_api_access_token_scopes",
            ["id", "token_id", "scope_type", "value", "created_by", "created_at"],
            (
                scope_id,
                token_id,
                normalized_type,
                normalized_value,
                created_by,
                created_at,
            ),
        )
        row = self._db.fetch_one(
            """SELECT * FROM auth_api_access_token_scopes
               WHERE token_id = ? AND scope_type = ? AND value = ?""",
            (token_id, normalized_type, normalized_value),
        )
        if not row:
            raise ValueError("Failed to create token scope")
        _invalidate_api_token_verify_cache()
        return dict(row)

    def remove_api_access_token_scope(self, token_id: int, scope_id: int) -> bool:
        cursor = self._db.execute(
            "DELETE FROM auth_api_access_token_scopes WHERE id = ? AND token_id = ?",
            (scope_id, token_id),
        )
        if cursor.rowcount > 0:
            _invalidate_api_token_verify_cache()
        return cursor.rowcount > 0

    def list_api_access_token_scopes(self, token_id: int) -> List[Dict[str, Any]]:
        rows = self._db.fetch_all(
            """SELECT * FROM auth_api_access_token_scopes
               WHERE token_id = ? ORDER BY created_at ASC""",
            (token_id,),
        )
        return [dict(row) for row in rows]

    def get_api_access_token_usage(
        self,
        token_id: int,
        recent_limit: int = 100,
    ) -> Dict[str, Any]:
        # One summary read feeds both the nested token's usage counters and the
        # top-level counters below. The row converter used to re-issue the same
        # COUNT(DISTINCT ip_index)/SUM(denied) SELECT for the same token; the
        # extra ``COUNT(*)`` here makes this the wider of the two queries, so
        # thread it through instead of paying for both.
        summary = (
            self._db.fetch_one(
                """SELECT COUNT(*) AS total_events,
                          COUNT(DISTINCT ip_index) AS distinct_ip_count,
                          SUM(CASE WHEN allowed = 0 THEN 1 ELSE 0 END) AS denied_count_total
                   FROM auth_api_access_token_events
                   WHERE token_id = ?""",
                (token_id,),
            )
            or {}
        )
        token = self.get_api_access_token(token_id, usage_summary=summary)
        if not token:
            raise UserNotFoundError("Access token not found")

        recent_rows = self._db.fetch_all(
            """SELECT * FROM auth_api_access_token_events
               WHERE token_id = ? ORDER BY used_at DESC LIMIT ?""",
            (token_id, recent_limit),
        )
        recent_events = [self._row_to_access_token_event(row) for row in recent_rows]

        # Each event's IP is sealed with AAD bound to its own row id
        # (``context=str(event_id)``), so the ciphertext of an arbitrary
        # member of the group cannot be decrypted. Join back to the most
        # recent event per ip_index and decrypt using that row's id, rather
        # than aggregating ciphertext with MAX() (which no context can open).
        ip_rows = self._db.fetch_all(
            """SELECT g.ip_index, g.max_id, e.ip_encrypted,
                      g.request_count, g.last_seen_at, g.denied_count
               FROM (
                   SELECT ip_index, MAX(id) AS max_id, COUNT(*) AS request_count,
                          MAX(used_at) AS last_seen_at,
                          SUM(CASE WHEN allowed = 0 THEN 1 ELSE 0 END) AS denied_count
                   FROM auth_api_access_token_events
                   WHERE token_id = ? AND ip_index IS NOT NULL
                   GROUP BY ip_index
               ) g
               JOIN auth_api_access_token_events e ON e.id = g.max_id
               ORDER BY g.request_count DESC, g.last_seen_at DESC
               LIMIT 50""",
            (token_id,),
        )
        top_ips = []
        for row in ip_rows:
            ip_address = None
            if row.get("ip_encrypted"):
                try:
                    ip_address = self.crypto.decrypt_data(
                        row["ip_encrypted"], context=str(row["max_id"])
                    )
                except Exception:
                    ip_address = "[decryption failed]"
            top_ips.append(
                {
                    "ip_address": ip_address or "UNKNOWN",
                    "request_count": int(row["request_count"] or 0),
                    "denied_count": int(row["denied_count"] or 0),
                    "last_seen_at": row["last_seen_at"],
                }
            )

        path_rows = self._db.fetch_all(
            """SELECT method, path, COUNT(*) AS request_count, MAX(used_at) AS last_seen_at
               FROM auth_api_access_token_events
               WHERE token_id = ? AND path IS NOT NULL
               GROUP BY method, path
               ORDER BY request_count DESC, last_seen_at DESC
               LIMIT 50""",
            (token_id,),
        )
        top_paths = [
            {
                "method": row["method"],
                "path": row["path"],
                "request_count": int(row["request_count"] or 0),
                "last_seen_at": row["last_seen_at"],
            }
            for row in path_rows
        ]

        return {
            "token": token,
            "scopes": self.list_api_access_token_scopes(token_id),
            "recent_events": recent_events,
            "top_ips": top_ips,
            "top_paths": top_paths,
            "total_events": int(summary.get("total_events") or 0),
            "distinct_ip_count": int(summary.get("distinct_ip_count") or 0),
            "denied_count_total": int(summary.get("denied_count_total") or 0),
        }

    def _introspect_api_access_token(
        self, token: str, ip_address: Optional[str]
    ) -> Dict[str, Any]:
        """Read-only, cacheable introspection of an API access token.

        Resolves the token row, proves possession of the *exact* secret and
        computes the scope/IP binding decision. Nothing is written here, so the
        small verdict is safe to memoise. Expiry is deliberately NOT decided in
        this method: the caller re-checks it against the live clock on every
        invocation (including a cache hit), so a warm verdict cannot keep an
        expired credential alive.
        """
        verdict: Dict[str, Any] = {
            "authentic": False,
            "token_id": None,
            "expires_at": None,
            "scope_mode": "none",
            "scope_match": None,
            "has_scopes": False,
            "last_used_at": 0,
            "first_used_at": None,
        }
        token_index = self.crypto.fast_blind_index(token, "api_access_token")
        legacy_index = self.crypto.legacy_fast_blind_index(token, "api_access_token")

        row = self._db.fetch_one(
            "SELECT * FROM auth_api_access_tokens WHERE token_index = ? OR (token_index = ? AND ? != '')",
            (token_index, legacy_index, legacy_index),
        )
        if not row or row["revoked"]:
            return verdict
        row = dict(row)

        # The blind index is a *search* structure, not a credential check.
        # ``blind_index`` deliberately lowercases/strips its input so it can
        # find encrypted e-mail addresses regardless of casing; for an API
        # token that means ``token``, ``token.lower()`` and ``"  token  "``
        # all resolve to the same row. Without an exact comparison here, any
        # case/whitespace-normalized form of a leaked token authenticates as
        # its owner (and the credential's effective search space shrinks).
        # Re-derive the plaintext from the authenticated ciphertext (AES-GCM
        # with the row id as AAD) and require a constant-time exact match.
        # Fail closed on a missing/corrupt/undecryptable stored secret.
        try:
            stored_token = self.crypto.decrypt_data(
                row["token_encrypted"], context=str(row["id"])
            )
        except Exception as exc:
            logger.debug(f"api token verify: stored secret undecryptable: {exc}")
            return verdict
        if not isinstance(stored_token, str) or not isinstance(token, str):
            return verdict
        if not hmac.compare_digest(token, stored_token):
            return verdict

        scopes = self.list_api_access_token_scopes(int(row["id"]))
        verdict.update(
            {
                "authentic": True,
                "token_id": int(row["id"]),
                "expires_at": row.get("expires_at"),
                "scope_mode": self._normalize_access_token_scope_mode(
                    row.get("scope_mode") or "none"
                ),
                "scope_match": self._match_api_access_token_scope(ip_address, scopes),
                "has_scopes": bool(scopes),
                "last_used_at": row.get("last_used_at") or 0,
                "first_used_at": row.get("first_used_at"),
            }
        )
        return verdict

    def verify_api_access_token(
        self,
        token: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        path: Optional[str] = None,
        method: Optional[str] = None,
    ) -> bool:
        if not token or not isinstance(token, str):
            return False

        # Cache key is a digest of the exact credential plus a digest of the
        # presented binding context; the raw value never enters the keyspace.
        key = _api_token_verify_key(token, ip_address, user_agent)
        verdict = _API_TOKEN_VERIFY_CACHE.get(key, _API_TOKEN_VERIFY_MISS)
        if verdict is _API_TOKEN_VERIFY_MISS:
            verdict = self._introspect_api_access_token(token, ip_address)
            _API_TOKEN_VERIFY_CACHE.set(key, verdict)

        if not verdict["authentic"]:
            return False

        token_id = verdict["token_id"]
        now = self._get_timestamp()

        # Revocation/binding are enforced by eager invalidation + the short TTL
        # (see the cache docstring); expiry is re-checked here even on a hit.
        # Revalidate a persisted expiry: a corrupt falsy value (0/"") or an
        # out-of-range forged future value must fail closed; only NULL means
        # "never expires". is_expired() denies zero/malformed/out-of-range.
        expires_at = verdict.get("expires_at")
        if expires_at is not None and is_expired(expires_at, now):
            self._record_api_access_token_event(
                token_id=int(token_id),
                ip_address=ip_address,
                user_agent=user_agent,
                path=path,
                method=method,
                allowed=False,
                scope_match=None,
                reject_reason="expired",
            )
            return False

        scope_match = verdict["scope_match"]
        if (
            verdict["has_scopes"]
            and verdict["scope_mode"] == "enforce"
            and not scope_match
        ):
            self._record_api_access_token_event(
                token_id=int(token_id),
                ip_address=ip_address,
                user_agent=user_agent,
                path=path,
                method=method,
                allowed=False,
                scope_match=False,
                reject_reason="ip_scope_denied",
            )
            return False

        # Mutable side effects stay OUTSIDE the cache: usage accounting and
        # auditing must reflect every request, not just cache misses. The single
        # combined UPDATE below already persists ``last_used_at``; a separate
        # "SET last_used_at = ?" write (formerly gated on the cached value being
        # older than a minute) re-wrote the identical ``now`` and was removed.
        updates = [
            "last_used_at = ?",
            "ua_index = ?",
            "ua_encrypted = ?",
            "last_used_path = ?",
            "use_count_total = COALESCE(use_count_total, 0) + 1",
        ]
        params: List[Any] = [
            now,
            self._ua_index(user_agent),
            self._encrypt_ua(user_agent, str(token_id)),
            path,
        ]
        if not verdict.get("first_used_at"):
            updates.append("first_used_at = ?")
            params.append(now)
        if ip_address:
            updates.append("last_used_ip_index = ?")
            updates.append("last_used_ip_encrypted = ?")
            params.extend(
                [
                    self.crypto.fast_blind_index(ip_address, "ip_address"),
                    self.crypto.encrypt_data(ip_address, context=str(token_id)),
                ]
            )
        params.append(token_id)
        self._db.execute(
            f"UPDATE auth_api_access_tokens SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )
        self._record_api_access_token_event(
            token_id=int(token_id),
            ip_address=ip_address,
            user_agent=user_agent,
            path=path,
            method=method,
            allowed=True,
            scope_match=scope_match,
            reject_reason=None,
        )
        return True

    @cached(ttl=30, prefix="access_token_required")
    def is_api_access_token_required(self) -> bool:
        try:
            from src.api.config import get_api_config

            api_cfg = get_api_config()
            if not api_cfg.access_token_required:
                return False
        except Exception as e:
            logger.error(
                f"is_api_access_token_required: failed to read access token config: {e}"
            )
            return True
        try:
            now = self._get_timestamp()
            row = self._db.fetch_one(
                """SELECT id FROM auth_api_access_tokens
                   WHERE revoked = 0 AND (expires_at IS NULL OR expires_at > ?)
                   LIMIT 1""",
                (now,),
            )
            return bool(row)
        except Exception as e:
            logger.error(
                f"is_api_access_token_required: failed to query access tokens: {e}"
            )
            return True

    def _normalize_access_token_scope_mode(self, scope_mode: str) -> str:
        normalized = (scope_mode or "none").strip().lower()
        if normalized not in {"none", "monitor", "enforce"}:
            raise ValueError("Invalid access token scope mode")
        return normalized

    def _normalize_access_token_scope(
        self,
        scope_type: str,
        value: str,
    ) -> Tuple[str, str]:
        normalized_type = (scope_type or "").strip().lower()
        normalized_value = (value or "").strip()
        if normalized_type not in {"ip", "cidr"}:
            raise ValueError("Invalid access token scope type")
        if not normalized_value:
            raise ValueError("Access token scope value is required")
        try:
            if normalized_type == "ip":
                normalized_value = str(ipaddress.ip_address(normalized_value))
            else:
                normalized_value = str(
                    ipaddress.ip_network(normalized_value, strict=False)
                )
        except ValueError as exc:
            raise ValueError("Invalid access token scope value") from exc
        return normalized_type, normalized_value

    def _match_api_access_token_scope(
        self,
        ip_address: Optional[str],
        scopes: List[Dict[str, Any]],
    ) -> Optional[bool]:
        if not scopes:
            return True
        if not ip_address:
            return False
        try:
            current_ip = ipaddress.ip_address(ip_address)
        except ValueError:
            return False
        for scope in scopes:
            scope_type = (scope.get("scope_type") or "").lower()
            value = scope.get("value") or ""
            try:
                if scope_type == "ip" and current_ip == ipaddress.ip_address(value):
                    return True
                if scope_type == "cidr" and current_ip in ipaddress.ip_network(
                    value, strict=False
                ):
                    return True
            except ValueError:
                continue
        return False

    def _record_api_access_token_event(
        self,
        token_id: int,
        ip_address: Optional[str],
        user_agent: Optional[str],
        path: Optional[str],
        method: Optional[str],
        allowed: bool,
        scope_match: Optional[bool],
        reject_reason: Optional[str],
    ) -> None:
        event_id = self._generate_id()
        ip_index = None
        ip_encrypted = None
        if ip_address:
            ip_index = self.crypto.fast_blind_index(ip_address, "ip_address")
            ip_encrypted = self.crypto.encrypt_data(ip_address, context=str(event_id))

        ua_index = self._ua_index(user_agent)
        ua_encrypted = self._encrypt_ua(user_agent, str(event_id))

        self._db.execute(
            """INSERT INTO auth_api_access_token_events
               (id, token_id, used_at, ip_index, ip_encrypted, method, path, ua_index, ua_encrypted, allowed, scope_match, reject_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                token_id,
                self._get_timestamp(),
                ip_index,
                ip_encrypted,
                method,
                path,
                ua_index,
                ua_encrypted,
                1 if allowed else 0,
                None if scope_match is None else (1 if scope_match else 0),
                reject_reason,
            ),
        )

    def _row_to_access_token_event(self, row: Dict[str, Any]) -> Dict[str, Any]:
        row = dict(row)
        ua = None
        if row.get("ua_encrypted"):
            try:
                ua = self.crypto.decrypt_data(
                    row["ua_encrypted"], context=str(row["id"])
                )
            except Exception:
                ua = "[decryption failed]"

        ip_address = None
        if row.get("ip_encrypted"):
            try:
                ip_address = self.crypto.decrypt_data(
                    row["ip_encrypted"], context=str(row["id"])
                )
            except Exception:
                ip_address = "[decryption failed]"

        return {
            "id": row["id"],
            "used_at": row["used_at"],
            "ip_address": ip_address,
            "method": row["method"],
            "path": row["path"],
            "user_agent": ua,
            "allowed": bool(row["allowed"]),
            "scope_match": None
            if row.get("scope_match") is None
            else bool(row["scope_match"]),
            "reject_reason": row.get("reject_reason"),
        }

    def _row_to_access_token(
        self,
        row: Dict[str, Any],
        distinct_summary: Optional[Dict[str, Any]] = None,
    ) -> AccessToken:
        row = dict(row)
        use_count = int(row.get("use_count_total") or 0)
        if distinct_summary is None:
            # Single-token callers (create/get/update/rotate) still resolve the
            # aggregates on demand; only the listing batches them up front.
            if use_count > 0:
                distinct_summary = (
                    self._db.fetch_one(
                        """SELECT COUNT(DISTINCT ip_index) AS distinct_ip_count,
                              SUM(CASE WHEN allowed = 0 THEN 1 ELSE 0 END) AS denied_count_total
                        FROM auth_api_access_token_events
                        WHERE token_id = ?""",
                        (row["id"],),
                    )
                    or {}
                )
            else:
                distinct_summary = {}
        # Narrow Optional[Dict] -> Dict for callers that supplied summaries.
        distinct_summary = distinct_summary or {}
        last_ip = None
        if row.get("last_used_ip_encrypted"):
            try:
                last_ip = self.crypto.decrypt_data(
                    row["last_used_ip_encrypted"], context=str(row["id"])
                )
            except Exception:
                last_ip = "[decryption failed]"
        return AccessToken(
            id=row["id"],
            name=row.get("name"),
            description=row.get("description"),
            created_by=row.get("created_by"),
            created_at=row["created_at"],
            first_used_at=row.get("first_used_at"),
            last_used_at=row.get("last_used_at"),
            last_used_ip_address=last_ip,
            last_used_user_agent=self.crypto.decrypt_data(
                row["ua_encrypted"], context=str(row["id"])
            )
            if row.get("ua_encrypted")
            else None,
            last_used_path=row.get("last_used_path"),
            expires_at=row.get("expires_at"),
            scope_mode=row.get("scope_mode") or "none",
            use_count_total=use_count,
            distinct_ip_count=int(distinct_summary.get("distinct_ip_count") or 0),
            denied_count_total=int(distinct_summary.get("denied_count_total") or 0),
            revoked=bool(row["revoked"]),
            revoked_at=row.get("revoked_at"),
            revoked_by=row.get("revoked_by"),
        )
