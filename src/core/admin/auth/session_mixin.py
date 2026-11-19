"""
Session management mixin providing create_session, validate_session, and logout.
"""

import secrets
import time
from typing import Optional

from typing import Any


from .helpers import _hash_admin_token
from ..request_roles import (
    clear_session_roles,
    remember_session_credential,
    remember_session_roles,
)


def _row_value(row, key: str, index: int, default=None):
    """Read a row value consistently across dict and tuple drivers."""
    if isinstance(row, dict):
        value = row.get(key, default)
    else:
        value = row[index] if len(row) > index else default
    return default if value is None else value


class SessionMixin:
    _db: Any

    """Session lifecycle management."""

    def create_session(self, admin_id: int, expires_hours: int = 8) -> str:
        """Create admin session."""
        from src.utils.encryption import generate_snowflake_id

        token = secrets.token_urlsafe(32)
        token_hash = _hash_admin_token(token)
        now = int(time.time())
        # Admin session timestamps are stored and compared in seconds.
        # Keep the expiry in the same unit; multiplying by 1000 made an
        # eight-hour session valid for roughly 333 days.
        expires = now + (expires_hours * 3600)
        sid = generate_snowflake_id()
        self._db.execute(
            "INSERT INTO admin_sessions (id, admin_id, token, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (sid, admin_id, token_hash, now, expires),
        )
        return token

    def validate_session(self, token: str) -> Optional[int]:
        """Validate an admin session token.

        The caller's role permission payloads are selected in this same
        statement and cached for the request, so the permission gate does not
        need a second ``admin_roles`` round trip. The LEFT JOIN always yields
        one row per matching session (NULL permissions for an admin with no
        roles), so the admin id and the exact expiry/hash guard are unchanged.

        A session also stops being valid the moment its account is locked.
        Locking an admin writes ``auth_users.account_locked = 1`` and revokes
        ``auth_sessions``, but it never deletes the rows in ``admin_sessions``
        that this method authenticates against. Without the guard below, a
        locked/disabled administrator could keep using a previously issued
        admin bearer token with full privileges. The ``auth_users`` row may
        legitimately be absent (bootstrap/test installs), so only an explicit
        lock flag invalidates the session.
        """
        now = int(time.time())
        token_hash = _hash_admin_token(token)
        rows = self._db.fetch_all(
            """
            SELECT s.admin_id, r.permissions
            FROM admin_sessions s
            LEFT JOIN admin_role_assignments a ON a.admin_id = s.admin_id
            LEFT JOIN admin_roles r ON r.id = a.role_id
            WHERE s.token = ? AND s.expires_at > ?
              AND NOT EXISTS (
                  SELECT 1 FROM auth_users u
                  WHERE u.id = s.admin_id AND COALESCE(u.account_locked, 0) = 1
              )
            """,
            (token_hash, now),
        )
        if not rows:
            # Raw bearer values from pre-hardened deployments are deliberately
            # no longer accepted. They cannot be safely distinguished from a
            # leaked token at validation time; administrators must sign in
            # again so a deterministic fingerprint is stored instead.
            # A locked account takes the same path. Drop any roles a prior
            # validation in this context may have cached so they cannot be
            # reused by a later permission check.
            clear_session_roles()
            return None

        admin_id = _row_value(rows[0], "admin_id", 0)
        if admin_id is None:
            return None
        remember_session_roles(
            self._db,
            admin_id,
            tuple(_row_value(row, "permissions", 1) for row in rows),
        )
        # Bind the validated session's one-way fingerprint so the short-lived
        # role-grant cache can partition grants by caller. The raw token never
        # leaves this method.
        remember_session_credential(token_hash)
        return admin_id

    def logout(self, token: str) -> bool:
        """Invalidate admin session."""
        token_hash = _hash_admin_token(token)
        self._db.execute(
            "DELETE FROM admin_sessions WHERE token = ? OR token = ?",
            (token_hash, token),
        )
        return True
