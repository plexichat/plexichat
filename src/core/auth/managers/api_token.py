import secrets
import ipaddress
from typing import Optional, List, Dict, Any, Tuple

from src.core.database import cached, invalidate_pattern

from ..models import AccessToken, AuditEventType
from ..exceptions import UserNotFoundError


from .protocol import AuthManagerProtocol


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
        row = self._db.fetch_one(
            "SELECT * FROM auth_api_access_tokens WHERE id = ?", (token_id,)
        )
        access_token = self._row_to_access_token(row)
        access_token.token = token
        return access_token

    def list_api_access_tokens(self, include_revoked: bool = True) -> List[AccessToken]:
        now = self._get_timestamp()
        if include_revoked:
            rows = self._db.fetch_all(
                "SELECT * FROM auth_api_access_tokens ORDER BY created_at DESC"
            )
        else:
            rows = self._db.fetch_all(
                """SELECT * FROM auth_api_access_tokens
                   WHERE revoked = 0 AND (expires_at IS NULL OR expires_at > ?)
                   ORDER BY created_at DESC""",
                (now,),
            )
        return [self._row_to_access_token(row) for row in rows]

    def get_api_access_token(self, token_id: int) -> Optional[AccessToken]:
        row = self._db.fetch_one(
            "SELECT * FROM auth_api_access_tokens WHERE id = ?", (token_id,)
        )
        if not row:
            return None
        return self._row_to_access_token(row)

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
        return dict(row)

    def remove_api_access_token_scope(self, token_id: int, scope_id: int) -> bool:
        cursor = self._db.execute(
            "DELETE FROM auth_api_access_token_scopes WHERE id = ? AND token_id = ?",
            (scope_id, token_id),
        )
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
        token = self.get_api_access_token(token_id)
        if not token:
            raise UserNotFoundError("Access token not found")

        recent_rows = self._db.fetch_all(
            """SELECT * FROM auth_api_access_token_events
               WHERE token_id = ? ORDER BY used_at DESC LIMIT ?""",
            (token_id, recent_limit),
        )
        recent_events = [self._row_to_access_token_event(row) for row in recent_rows]

        ip_rows = self._db.fetch_all(
            """SELECT ip_index, MAX(ip_encrypted) AS ip_encrypted, COUNT(*) AS request_count,
                      MAX(used_at) AS last_seen_at,
                      SUM(CASE WHEN allowed = 0 THEN 1 ELSE 0 END) AS denied_count
               FROM auth_api_access_token_events
               WHERE token_id = ? AND ip_index IS NOT NULL
               GROUP BY ip_index
               ORDER BY request_count DESC, last_seen_at DESC
               LIMIT 50""",
            (token_id,),
        )
        top_ips = []
        for row in ip_rows:
            ip_address = None
            if row.get("ip_encrypted"):
                try:
                    ip_address = self.crypto.decrypt_data(
                        row["ip_encrypted"], context="api_access_token_event"
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

    def verify_api_access_token(
        self,
        token: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        path: Optional[str] = None,
        method: Optional[str] = None,
    ) -> bool:
        if not token:
            return False
        token_index = self.crypto.fast_blind_index(token, "api_access_token")
        legacy_index = self.crypto.legacy_fast_blind_index(token, "api_access_token")

        row = self._db.fetch_one(
            "SELECT * FROM auth_api_access_tokens WHERE token_index = ? OR (token_index = ? AND ? != '')",
            (token_index, legacy_index, legacy_index),
        )
        if not row or row["revoked"]:
            return False
        row = dict(row)
        now = self._get_timestamp()
        if row.get("expires_at") and int(row["expires_at"]) <= now:
            self._record_api_access_token_event(
                token_id=int(row["id"]),
                ip_address=ip_address,
                user_agent=user_agent,
                path=path,
                method=method,
                allowed=False,
                scope_match=None,
                reject_reason="expired",
            )
            return False

        scope_mode = self._normalize_access_token_scope_mode(
            row.get("scope_mode") or "none"
        )
        scopes = self.list_api_access_token_scopes(int(row["id"]))
        scope_match = self._match_api_access_token_scope(ip_address, scopes)
        if scopes and scope_mode == "enforce" and not scope_match:
            self._record_api_access_token_event(
                token_id=int(row["id"]),
                ip_address=ip_address,
                user_agent=user_agent,
                path=path,
                method=method,
                allowed=False,
                scope_match=False,
                reject_reason="ip_scope_denied",
            )
            return False

        now = self._get_timestamp()
        last_used = row.get("last_used_at") or 0
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
            self._encrypt_ua(user_agent, str(row["id"])),
            path,
        ]
        if not row.get("first_used_at"):
            updates.append("first_used_at = ?")
            params.append(now)
        if ip_address:
            updates.append("last_used_ip_index = ?")
            updates.append("last_used_ip_encrypted = ?")
            params.extend(
                [
                    self.crypto.fast_blind_index(ip_address, "ip_address"),
                    self.crypto.encrypt_data(ip_address, context=str(row["id"])),
                ]
            )
        params.append(row["id"])
        self._db.execute(
            f"UPDATE auth_api_access_tokens SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )
        if now - int(last_used) > 60000:
            self._db.execute(
                "UPDATE auth_api_access_tokens SET last_used_at = ? WHERE id = ?",
                (now, row["id"]),
            )
        self._record_api_access_token_event(
            token_id=int(row["id"]),
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
            now = self._get_timestamp()
            row = self._db.fetch_one(
                """SELECT id FROM auth_api_access_tokens
                   WHERE revoked = 0 AND (expires_at IS NULL OR expires_at > ?)
                   LIMIT 1""",
                (now,),
            )
            return bool(row)
        except Exception:
            return False

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

    def _row_to_access_token(self, row: Dict[str, Any]) -> AccessToken:
        row = dict(row)
        use_count = int(row.get("use_count_total") or 0)
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
