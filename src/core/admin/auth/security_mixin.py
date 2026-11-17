"""
Security status mixin providing get_security_status.
"""

import json
from typing import Optional

import utils.config as config

from typing import Any


from .dataclasses import AdminSecurityStatus


class SecurityMixin:
    _db: Any

    """Security posture and metadata."""

    def get_security_status(self, admin_id: int) -> Optional[AdminSecurityStatus]:
        """Return security settings and posture for an admin account."""
        row = self._db.fetch_one(
            """
            SELECT username, email, created_at, last_login, totp_enabled, must_setup_otp,
                   backup_codes, backup_codes_hash
            FROM admin_users
            WHERE id = ?
            """,
            (admin_id,),
        )
        if not row:
            return None
        if isinstance(row, dict):
            username = row["username"]
            email = row["email"]
            created_at = row["created_at"]
            last_login = row["last_login"]
            totp_enabled = bool(row["totp_enabled"])
            must_setup_otp = bool(row["must_setup_otp"])
            backup_codes = row["backup_codes"] or ""
            hashed = row.get("backup_codes_hash")
        else:
            (
                username,
                email,
                created_at,
                last_login,
                totp_enabled,
                must_setup_otp,
                backup_codes,
                *extra,
            ) = row
            hashed = extra[0] if extra else None

        # backup_codes_hash is selected alongside the rest of the row above;
        # no second admin_users read is needed to count remaining codes.
        if hashed:
            try:
                remaining = len(json.loads(hashed))
            except (json.JSONDecodeError, TypeError):
                remaining = 0
        else:
            remaining = len(
                [code for code in str(backup_codes or "").split(",") if code.strip()]
            )
        admin_config = config.get("admin_ui", {})
        return AdminSecurityStatus(
            admin_id=admin_id,
            username=username,
            email=email,
            created_at=created_at,
            last_login=last_login,
            otp_required=bool(admin_config.get("require_otp", True)),
            otp_enabled=bool(totp_enabled),
            must_setup_otp=bool(must_setup_otp),
            backup_codes_remaining=remaining,
        )
