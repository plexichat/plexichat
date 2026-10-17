"""
Session management mixin providing create_session, validate_session, and logout.
"""

import secrets
import time
from typing import Optional

from typing import Any


from .helpers import _hash_admin_token


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
        """Validate admin session token."""
        now = int(time.time())
        token_hash = _hash_admin_token(token)
        row = self._db.fetch_one(
            "SELECT id, admin_id, token FROM admin_sessions WHERE token = ? AND expires_at > ?",
            (token_hash, now),
        )
        if not row:
            # Raw bearer values from pre-hardened deployments are deliberately
            # no longer accepted. They cannot be safely distinguished from a
            # leaked token at validation time; administrators must sign in
            # again so a deterministic fingerprint is stored instead.
            return None

        if isinstance(row, dict):
            admin_id = row["admin_id"]
        else:
            admin_id = row[1]
        return admin_id

    def logout(self, token: str) -> bool:
        """Invalidate admin session."""
        token_hash = _hash_admin_token(token)
        self._db.execute(
            "DELETE FROM admin_sessions WHERE token = ? OR token = ?",
            (token_hash, token),
        )
        return True
